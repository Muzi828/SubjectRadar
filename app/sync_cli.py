from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, db, sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="SafeLens 期刊同步任务")
    parser.add_argument("--trigger", default="manual", choices=["manual", "scheduled", "startup", "probe"])
    parser.add_argument("--journal", action="append", default=[], help="期刊 id 或 slug，可重复")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_DIR / "sync.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    db.init_db()
    logging.getLogger("safelens.cli").info("同步开始 trigger=%s journals=%s", args.trigger, args.journal or "all")
    ids = []
    if args.journal:
        journals = db.list_journals()
        by_id = {str(j["id"]): j for j in journals}
        by_slug = {j["slug"]: j for j in journals}
        for token in args.journal:
            journal = by_id.get(str(token)) or by_slug.get(token)
            if not journal:
                print(f"找不到期刊：{token}", file=sys.stderr)
                return 2
            ids.append(journal["id"])

    def progress(payload):
        if not args.quiet and payload.get("message"):
            print(f"[{payload.get('phase','')}] {payload['message']}", flush=True)

    try:
        summary = sync.sync_all(trigger=args.trigger, journal_ids=ids or None, progress=progress)
    except sync.SyncBusy as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        logging.getLogger("safelens.cli").exception("同步失败")
        print(f"同步失败：{exc}", file=sys.stderr)
        return 1
    logging.getLogger("safelens.cli").info(
        "同步结束 status=%s new=%s updated=%s errors=%s",
        summary.get("status"), summary.get("new_count"), summary.get("updated_count"), summary.get("error_count"),
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary.get("status") in ("done", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
