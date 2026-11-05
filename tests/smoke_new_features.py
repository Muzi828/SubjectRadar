"""新功能冒烟测试：作者追踪 / 引用刷新 / 本地PDF / 相关论文。使用临时数据目录，不影响真实库。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

_tmp = Path(tempfile.mkdtemp(prefix="safelens-smoke-"))
os.environ["SAFELENS_DATA_DIR"] = str(_tmp / "data")
os.environ["SAFELENS_LOG_DIR"] = str(_tmp / "logs")

from app import config, db, server  # noqa: E402

failures = []


def check(name, cond, extra=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    if not cond:
        failures.append(name)


def main() -> int:
    db.init_db()
    journals = db.list_journals()
    check("建库并种子期刊", len(journals) >= 17, f"journals={len(journals)}")

    # 造三篇测试论文
    ids = []
    base = dict(authors=["张三", "李四"], abstract="", source="rss")
    specs = [
        dict(fingerprint="smoke-1", doi="10.1000/smoke.1", title="Smoke test one",
             authors=["Alice Reason", "Bob"], journal_id=journals[0]["id"],
             topics=["安全管理"], keywords=["risk assessment"], abstract="Risk study"),
        dict(fingerprint="smoke-2", doi="10.1000/smoke.2", title="Smoke test two",
             authors=["Alice Reason"], journal_id=journals[0]["id"],
             topics=["安全管理", "安全文化"], keywords=["safety culture"]),
        dict(fingerprint="smoke-3", doi="10.1000/smoke.3", title="Smoke test three",
             authors=["王五"], journal_id=journals[1]["id"],
             topics=["火灾与消防"], keywords=["fire"]),
    ]
    for spec in specs:
        pid, is_new = db.upsert_paper(spec)
        ids.append(pid)
        check(f"插入论文 {spec['fingerprint']}", is_new and pid > 0)

    # ---- 作者追踪 ----
    db.save_settings({"watched_authors": ["Alice Reason"]})
    check("保存关注作者", db.watched_authors() == ["Alice Reason"])
    expr, params = db.author_watch_expression()
    with db.db() as conn:
        hit = conn.execute(
            f"SELECT COUNT(*) c FROM papers p WHERE {expr}", params).fetchone()["c"]
    check("作者命中 2 篇", hit == 2, f"hit={hit}")
    result = db.list_papers(view="watch")
    check("watch 视图返回 2 篇", result["total"] == 2, f"total={result['total']}")
    check("列表带 watch_match 与 has_local_pdf 字段",
          all("watch_match" in p and "has_local_pdf" in p for p in result["items"]))
    digest = db.get_digest("today", focus="interests")
    check("简报我的关注含作者命中", digest["count"] >= 2, f"count={digest['count']}")

    # ---- 被引数刷新 ----
    check("首次刷新到期", db.citation_refresh_due(days=6))
    changed = db.update_citation(ids[0], 100)
    check("引用 0→100 更新", changed and db.get_paper(ids[0])["citation_count"] == 100)
    check("0→100 触发引用上升", db.get_paper(ids[0])["citation_rising"] is True)
    changed = db.update_citation(ids[0], 101)
    check("101→102 小增量不上升",
          (not db.update_citation(ids[0], 102)) or db.get_paper(ids[0])["citation_rising"] is False)
    changed = db.update_citation(ids[1], 6)
    check("0→6 低于阈值不上升", db.get_paper(ids[1])["citation_rising"] is False)
    db.mark_citation_refreshed()
    check("标记刷新后未到期", not db.citation_refresh_due(days=6))
    n = db.bulk_update(ids[:2], "clear_rising")
    check("批量清除引用提醒", n >= 1)

    # ---- 本地 PDF ----
    pdf_file = db.local_pdf_path(ids[0])
    pdf_file.write_bytes(b"%PDF-1.4 smoke")
    db.save_local_pdf(ids[0], pdf_file.stat().st_size)
    check("PDF 记录保存", db.get_local_pdf(ids[0]) is not None)
    stats = db.local_pdf_stats()
    check("PDF 统计", stats["count"] == 1 and stats["bytes"] > 0, f"stats={stats}")
    filtered = db.list_papers(view="all", pdf=True)
    check("pdf=1 过滤", filtered["total"] == 1 and filtered["items"][0]["has_local_pdf"])
    pdf_file.unlink()
    pruned = db.prune_missing_local_pdfs()
    check("清理失效 PDF 记录", pruned == 1 and db.local_pdf_stats()["count"] == 0)

    # ---- 相关论文 ----
    related = db.related_papers(ids[0])
    check("相关论文推荐到 smoke-2", any(p["id"] == ids[1] for p in related),
          f"related={[p['id'] for p in related]}")
    if related:
        check("推荐理由字段", bool(related[0].get("related_reasons")))

    # ---- HTTP 接口冒烟 ----
    port = 8791
    httpd = server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
    httpd.state = server.AppState()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)

    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    boot = get("/api/bootstrap")
    check("bootstrap 带新统计", "watch_new" in boot["summary"] and "local_pdf_count" in boot["summary"])
    watch = get("/api/papers?view=watch")
    check("HTTP watch 视图", watch["total"] == 2)
    rel = get(f"/api/papers/{ids[0]}/related")
    check("HTTP related 接口", len(rel["items"]) >= 1)
    lp = get("/api/local-pdfs")
    check("HTTP local-pdfs 接口", lp["stats"]["count"] == 0)
    rising_list = get("/api/papers?view=all&rising=0")
    check("HTTP rising 过滤参数可用", rising_list["total"] >= 1)
    httpd.shutdown()

    print()
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)
    sys.exit(code)
