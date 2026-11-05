"""每日快照构建脚本（GitHub Actions 使用）。

从 17 本期刊的官网 RSS / 目录 / API 抓取最新论文，用 Crossref 与 OpenAlex
补齐元数据，生成 docs/data/snapshot.json 作为在线版的底库。

用法：python tools/build_snapshot.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = Path(tempfile.mkdtemp(prefix="safelens-sync-"))
os.environ.setdefault("SAFELENS_DATA_DIR", str(_tmp / "data"))
os.environ.setdefault("SAFELENS_LOG_DIR", str(_tmp / "logs"))

from app import config, sources  # noqa: E402

FIELDS = [
    "fingerprint", "doi", "pii", "title", "authors", "authors_en",
    "journal_id", "journal_name", "journal_short", "journal_color", "journal_publisher",
    "published_date", "published_precision", "online_date", "volume", "issue", "pages",
    "abstract", "abstract_en", "title_en", "keywords", "keywords_en", "topics",
    "url", "doi_url", "pdf_url", "preview_url", "citation_text", "citation_text_en",
    "citation_count", "open_access", "first_seen_at", "affiliations", "funding",
]
LIST_FIELDS = {"authors", "authors_en", "keywords", "keywords_en", "topics"}


def paper_key(p: dict) -> str:
    if p.get("fingerprint"):
        return "fp:" + p["fingerprint"]
    if p.get("doi"):
        return "doi:" + str(p["doi"]).lower()
    if p.get("pii"):
        return "pii:" + p["pii"]
    return "jt:" + (p.get("journal_name") or "") + "|" + (p.get("title") or "").lower().strip()


def main() -> int:
    out_path = ROOT / "docs" / "data" / "snapshot.json"
    previous: dict[str, dict] = {}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            for p in old.get("papers", []):
                previous[paper_key(p)] = p
        except Exception:
            pass

    journals_out = []
    papers_out: dict[str, dict] = {}
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    failures = []

    for journal in config.JOURNALS:
        jid = int(journal["sort_order"])
        journals_out.append({
            "id": jid,
            "name": journal["name"],
            "short_name": journal["short_name"],
            "publisher": journal["publisher"],
            "issn": journal["issn"],
            "color": journal["color"],
            "homepage_url": journal["homepage_url"],
        })
        try:
            items, warning = sources.fetch_official_items(journal, limit=config.FEED_LIMIT)
            if not items:
                raise RuntimeError(warning or "官网没有返回条目")
            if journal.get("crossref_issn"):
                try:
                    works = sources.fetch_crossref_works(journal["crossref_issn"], days=180, rows=200)
                    if works:
                        items, _ = sources.merge_crossref(items, works)
                except Exception as exc:
                    print(f"[warn] {journal['name']} crossref: {exc}", file=sys.stderr)
            for item in items:
                item["journal_id"] = jid
                item["journal_name"] = journal["name"]
                item["journal_short"] = journal["short_name"]
                item["journal_color"] = journal["color"]
                item["journal_publisher"] = journal["publisher"]
                if item.get("doi"):
                    item["doi"] = item["doi"].lower()
                    item["doi_url"] = item.get("doi_url") or f"https://doi.org/{item['doi']}"
                item.setdefault("topics", [])
                if not item["topics"]:
                    item["topics"] = sources.build_topics(item.get("title", ""), item.get("abstract", ""), item.get("keywords") or [])
                key = paper_key(item)
                old = previous.get(key)
                if old:
                    for f in FIELDS:
                        v = item.get(f)
                        if v in (None, "", []):
                            item[f] = old.get(f)
                    item["first_seen_at"] = old.get("first_seen_at") or now
                else:
                    item["first_seen_at"] = now
                papers_out[key] = item
            print(f"[ok] {journal['name']}: {len(items)} 条")
        except Exception as exc:
            failures.append(journal["name"])
            print(f"[fail] {journal['name']}: {exc}", file=sys.stderr)
            # 保留该刊旧数据
            for key, p in previous.items():
                if p.get("journal_id") == jid:
                    papers_out[key] = p
        time.sleep(0.4)

    # 只保留仍出现在源里的论文 + 旧库中最近 120 天入库的（避免 RSS 窗口截断丢历史）
    cutoff = datetime.now().strftime("%Y-%m-%d")
    candidates = list(papers_out.values())
    final = []
    for p in candidates:
        first = str(p.get("first_seen_at") or "")
        age_days = (datetime.now() - datetime.strptime(first[:10], "%Y-%m-%d")).days if first else 9999
        if age_days <= 120 or p.get("doi"):
            final.append(p)

    # OpenAlex 补摘要（仅缺摘要的）
    abstract_candidates = [p for p in final if p.get("doi") and not p.get("abstract")][:80]
    if abstract_candidates:
        try:
            sources.enrich_openalex(abstract_candidates)
        except Exception as exc:
            print(f"[warn] openalex: {exc}", file=sys.stderr)

    payload = {
        "app": "SafeLens",
        "version": 1,
        "exported_at": now,
        "papers": [{f: (p.get(f) if f in LIST_FIELDS else p.get(f, "")) for f in FIELDS} for p in final],
        "journals": journals_out,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"snapshot: {len(final)} papers, {len(journals_out)} journals -> {out_path}")
    if failures:
        print("failed journals: " + ", ".join(failures), file=sys.stderr)
    return 0 if final else 1


if __name__ == "__main__":
    sys.exit(main())
