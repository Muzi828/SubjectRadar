"""导出文献库为静态版底库快照 data/snapshot.json。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402

FIELDS = [
    "id", "fingerprint", "doi", "pii", "title", "authors", "authors_en",
    "journal_id", "journal_name", "journal_short", "journal_color", "journal_publisher",
    "published_date", "published_precision", "online_date", "volume", "issue", "pages",
    "abstract", "abstract_en", "title_en", "keywords", "keywords_en", "topics",
    "url", "doi_url", "pdf_url", "preview_url", "citation_text", "citation_text_en",
    "citation_count", "open_access", "first_seen_at",
]


def main() -> int:
    with db.db() as conn:
        rows = conn.execute(
            """
            SELECT p.*, j.name AS journal_name, j.short_name AS journal_short,
                   j.color AS journal_color, j.publisher AS journal_publisher
            FROM papers p JOIN journals j ON j.id=p.journal_id
            WHERE p.archived=0 ORDER BY p.id
            """
        ).fetchall()
        journals = conn.execute(
            "SELECT id, sort_order, name, short_name, publisher, issn, color, homepage_url FROM journals ORDER BY sort_order, id"
        ).fetchall()
    id_map = {int(j["id"]): int(j["sort_order"]) for j in journals}
    papers = []
    for row in rows:
        item = {k: row[k] for k in FIELDS if k in row.keys()}
        item["journal_id"] = id_map.get(int(item["journal_id"]), int(item["journal_id"]))
        for key in ("authors", "authors_en", "keywords", "keywords_en", "topics"):
            item[key] = json.loads(item[key] or "[]")
        item["open_access"] = bool(item["open_access"])
        papers.append(item)
    journal_rows = []
    for j in journals:
        item = dict(j)
        item["id"] = int(j["sort_order"])
        item.pop("sort_order", None)
        journal_rows.append(item)
    payload = {
        "app": "SafeLens",
        "version": 1,
        "exported_at": db.now_iso(),
        "papers": papers,
        "journals": journal_rows,
    }
    out = ROOT / "safelens-web" / "dist" / "data" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"exported {len(papers)} papers, {len(journals)} journals -> {out} ({out.stat().st_size/1024/1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
