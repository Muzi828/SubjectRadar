from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Callable

from . import config, db, sources

log = logging.getLogger("safelens.sync")
ProgressFn = Callable[[dict[str, Any]], None] | None


def audit_paper(paper_id: int) -> dict[str, Any] | None:
    from .article_metadata import enrich_and_audit
    paper = db.get_paper(paper_id)
    if not paper:
        return None
    try:
        result = enrich_and_audit(paper)
    except Exception as exc:
        log.warning("原文审计失败 paper=%s: %s", paper_id, exc)
        result = {"metadata": {}, "audit": {"status": "failed", "checked_at": db.now_iso(),
                  "source_url": paper.get("url", ""), "fields": {}, "errors": [str(exc)]}}
    return db.save_metadata_audit(paper_id, result)


class SyncBusy(RuntimeError):
    pass


class SyncCancelled(RuntimeError):
    pass


class SyncLock:
    def __init__(self, path=config.LOCK_PATH):
        self.path = path
        self.acquired = False

    def acquire(self) -> bool:
        config.ensure_dirs()
        if self.path.exists():
            try:
                age = time.time() - self.path.stat().st_mtime
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                pid = int(payload.get("pid") or 0)
                alive = False
                if pid:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except OSError:
                        alive = False
                if alive and age < 7200:
                    return False
                if age < 7200 and not pid:
                    return False
            except Exception:
                if time.time() - self.path.stat().st_mtime < 7200:
                    return False
            try:
                self.path.unlink()
            except OSError:
                return False
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "started_at": db.now_iso()}, fh)
            self.acquired = True
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    def release(self) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.acquired = False


def _emit(progress: ProgressFn, **payload: Any) -> None:
    if progress:
        try:
            progress(payload)
        except Exception:
            log.debug("progress callback failed", exc_info=True)


def _with_journal_fingerprint(item: dict[str, Any], journal: dict[str, Any]) -> dict[str, Any]:
    if not item.get("fingerprint") or item["fingerprint"].startswith("doi:") and not item.get("doi"):
        item["fingerprint"] = sources.make_fingerprint(
            item.get("doi", ""), item.get("pii", ""), item["title"], journal["id"]
        )
    if item.get("fingerprint", "").startswith("doi:") and not item.get("doi"):
        item["fingerprint"] = sources.make_fingerprint("", item.get("pii", ""), item["title"], journal["id"])
    # Crossref 补充条目的 journal_id 是 0，这里统一修正。
    item["journal_id"] = journal["id"]
    return item


def _journal_sync(journal: dict[str, Any], progress: ProgressFn, done: int, total: int,
                  cancel_event=None) -> dict[str, Any]:
    started = time.time()
    if cancel_event and cancel_event.is_set():
        raise SyncCancelled("用户中断检查")
    baseline = not bool(journal.get("baseline_done"))
    _emit(progress, current=journal["name"], done=done, total=total,
          message=f"正在读取 {journal['name']} 官网…", phase="fetch")
    items, warning = sources.fetch_official_items(journal, limit=config.FEED_LIMIT)
    if cancel_event and cancel_event.is_set():
        raise SyncCancelled("用户中断检查")
    if not items:
        raise RuntimeError(warning or "官网没有返回可识别的论文条目")

    if journal.get("feed_type") in ("cssjj", "cast", "cnki", "jc2"):
        targets = []
        for item in items:
            _with_journal_fingerprint(item, journal)
            existing = db.get_paper_by_fingerprint(item["fingerprint"])
            need_detail = not existing or not existing.get("abstract")
            if journal.get("feed_type") in ("cast", "cnki", "jc2") and existing:
                need_detail = need_detail or not existing.get("doi") or not existing.get("keywords") or not existing.get("published_date")
            if need_detail:
                targets.append(item)
        if targets:
            _emit(progress, current=journal["name"], done=done, total=total,
                  message=f"正在补全《{journal['name']}》的中文元数据…", phase="enrich")
            if journal.get("feed_type") == "cssjj":
                sources.enrich_cssjj_details(targets)
            elif journal.get("feed_type") == "cast":
                sources.enrich_cast_details(targets)
            elif journal.get("feed_type") == "jc2":
                sources.enrich_jc2_details(targets)
            else:
                sources.enrich_cnki_details(targets)
            for item in targets:
                item["topics"] = sources.build_topics(item["title"], item.get("abstract", ""), item.get("keywords") or [])

    merged_count = len(items)
    supplements = []
    crossref_warning = ""
    if journal.get("crossref_issn"):
        try:
            works = sources.fetch_crossref_works(journal["crossref_issn"], days=180, rows=200)
            if works:
                items, supplements = sources.merge_crossref(items, works)
                merged_count = len(items)
        except Exception as exc:
            crossref_warning = f"Crossref 补充失败：{exc}"
            log.warning("%s: %s", journal["name"], crossref_warning)

    inserted = 0
    updated = 0
    new_items: list[dict[str, Any]] = []
    new_ids: list[int] = []
    for item in items:
        if cancel_event and cancel_event.is_set():
            raise SyncCancelled("用户中断检查")
        _with_journal_fingerprint(item, journal)
        if item.get("doi"):
            item["doi"] = item["doi"].lower()
            item["doi_url"] = item.get("doi_url") or f"https://doi.org/{item['doi']}"
        if not item.get("topics"):
            item["topics"] = sources.build_topics(item["title"], item.get("abstract", ""), item.get("keywords") or [])
        pid, is_new = db.upsert_paper(item, is_baseline=baseline)
        if is_new:
            inserted += 1
            new_items.append(item)
            new_ids.append(pid)
        else:
            updated += 1

    # 首次建库时只给少量条目做摘要补全，避免第一次启动等待过久；
    # 日常新增则全部尝试补全。
    abstract_candidates = [it for it in new_items if it.get("doi") and not it.get("abstract")]
    if baseline:
        abstract_candidates = abstract_candidates[:40]
    if cancel_event and cancel_event.is_set():
        raise SyncCancelled("用户中断检查")
    if abstract_candidates:
        _emit(progress, current=journal["name"], done=done, total=total,
              message=f"正在补全 {journal['name']} 的摘要…", phase="enrich")
        try:
            sources.enrich_openalex(abstract_candidates)
            for item in abstract_candidates:
                if item.get("abstract"):
                    item["topics"] = sources.build_topics(item["title"], item["abstract"], item.get("keywords") or [])
                    db.upsert_paper(item, is_baseline=True)
        except Exception as exc:
            log.warning("%s OpenAlex 补全失败: %s", journal["name"], exc)

    # New records are checked during daily sync; initial libraries are filled in bounded batches.
    audit_ids = list(dict.fromkeys((new_ids[:3] if baseline else new_ids) + db.metadata_audit_candidates(journal["id"], 3)))
    for paper_id in audit_ids:
        if cancel_event and cancel_event.is_set():
            raise SyncCancelled("用户中断检查")
        _emit(progress, current=journal["name"], done=done, total=total,
              message=f"审计 Agent 正在核查论文 {paper_id} 原文…", phase="audit")
        audit_paper(paper_id)

    status = "warning" if (warning or crossref_warning) else "ok"
    db.mark_journal_sync(journal["id"], status, "；".join(x for x in (warning, crossref_warning) if x), baseline=True)
    elapsed = round(time.time() - started, 1)
    return {
        "journal": journal["name"],
        "journal_id": journal["id"],
        "status": "ok",
        "baseline": baseline,
        "rss_items": merged_count - len(supplements),
        "supplements": len(supplements),
        "inserted": inserted,
        "updated": updated,
        "warning": "；".join(x for x in (warning, crossref_warning) if x),
        "seconds": elapsed,
    }


def sync_all(trigger: str = "manual", journal_ids: list[int] | None = None,
             progress: ProgressFn = None, cancel_event=None) -> dict[str, Any]:
    lock = SyncLock()
    if not lock.acquire():
        raise SyncBusy("已有同步任务正在运行，请稍后再试")

    run_id = db.create_sync_run(trigger)
    started = db.now_iso()
    journals = db.get_enabled_journals()
    if journal_ids:
        wanted = {int(i) for i in journal_ids}
        journals = [j for j in journals if j["id"] in wanted]
    total = len(journals)
    _emit(progress, run_id=run_id, running=True, trigger=trigger, started_at=started,
          total=total, done=0, current="", new=0, phase="start", message="开始检查期刊官网")

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    inserted_total = 0
    updated_total = 0
    citations_updated = 0
    citations_failed = 0
    deadline = time.time() + 25 * 60
    cancelled = False
    try:
        for index, journal in enumerate(journals, start=1):
            if cancel_event and cancel_event.is_set():
                cancelled = True
                break
            if time.time() > deadline:
                errors.append({"journal": "", "journal_id": 0, "error": "超过单次同步 20 分钟上限，已停止后续期刊"})
                break
            try:
                result = _journal_sync(journal, progress, index - 1, total, cancel_event)
                results.append(result)
                inserted_total += result["inserted"]
                updated_total += result["updated"]
                _emit(progress, run_id=run_id, running=True, trigger=trigger, started_at=started,
                      total=total, done=index, current=journal["name"], new=inserted_total,
                      phase="journal_done", message=f"{journal['name']}：收录 {result['inserted']} 篇")
            except SyncCancelled:
                cancelled = True
                break
            except Exception as exc:
                log.exception("同步期刊失败: %s", journal["name"])
                message = str(exc)
                errors.append({"journal": journal["name"], "journal_id": journal["id"], "error": message})
                db.mark_journal_sync(journal["id"], "error", message, baseline=False)
                _emit(progress, run_id=run_id, running=True, trigger=trigger, started_at=started,
                      total=total, done=index, current=journal["name"], new=inserted_total,
                      phase="journal_error", message=f"{journal['name']}：{message[:120]}")

        # 被引数定期刷新：距上次刷新超过 6 天时，检查结束后批量更新全库引用数。
        if not cancelled and db.citation_refresh_due(days=6) and time.time() < deadline:
            try:
                papers = db.papers_for_citation_refresh()
                if papers:
                    _emit(progress, running=True, phase="citations",
                          message=f"正在刷新被引数（{len(papers)} 篇，OpenAlex）…")
                    citations_updated, citations_failed = sources.fetch_openalex_citations(
                        papers, db.update_citation,
                        should_stop=lambda: (cancel_event and cancel_event.is_set()) or time.time() > deadline,
                    )
                    db.mark_citation_refreshed()
                    _emit(progress, running=True, phase="citations_done",
                          message=f"被引数刷新完成：更新 {citations_updated} 篇")
            except Exception as exc:
                log.warning("被引数刷新失败: %s", exc)
                errors.append({"journal": "", "journal_id": 0, "error": f"被引数刷新失败：{exc}"})

        if cancelled:
            status = "cancelled"
            errors.append({"journal": "", "journal_id": 0, "error": "用户中断检查"})
        elif total == 0:
            status = "done"
        elif not results:
            status = "failed"
        elif errors:
            status = "partial"
        else:
            status = "done"
        detail = {"results": results, "errors": errors,
                  "citations_updated": citations_updated, "citations_failed": citations_failed}
        db.finish_sync_run(run_id, status=status, journals_total=total, journals_ok=len(results),
                           new_count=inserted_total, updated_count=updated_total,
                           error_count=len(errors), detail=detail)
        summary = {
            "run_id": run_id, "status": status, "trigger": trigger,
            "journals_total": total, "journals_ok": len(results),
            "new_count": inserted_total, "updated_count": updated_total,
            "citations_updated": citations_updated, "citations_failed": citations_failed,
            "error_count": len(errors), "started_at": started,
            "finished_at": db.now_iso(), "errors": errors,
        }
        if cancelled:
            message = "检查已中断，已完成的条目已保存"
        elif citations_updated:
            message = (f"检查完成：新增收录 {inserted_total} 篇，更新 {updated_total} 篇"
                       f"，被引数更新 {citations_updated} 篇")
        else:
            message = f"检查完成：新增收录 {inserted_total} 篇，更新 {updated_total} 篇"
        _emit(progress, run_id=run_id, running=False, trigger=trigger, started_at=started,
              total=total, done=len(results), current="", new=inserted_total,
              phase="cancelled" if cancelled else "done",
              status=status, message=message)
        return summary
    except Exception as exc:
        db.finish_sync_run(run_id, status="failed", journals_total=total, journals_ok=len(results),
                           new_count=inserted_total, updated_count=updated_total,
                           error_count=len(errors) + 1,
                           detail={"results": results, "errors": errors + [{"journal": "", "error": str(exc)}]})
        _emit(progress, run_id=run_id, running=False, trigger=trigger, started_at=started,
              total=total, done=len(results), current="", new=inserted_total, phase="failed",
              status="failed", message=f"同步失败：{exc}")
        raise
    finally:
        lock.release()


def status_snapshot() -> dict[str, Any]:
    last = db.get_last_run()
    return {"last_run": last, "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
