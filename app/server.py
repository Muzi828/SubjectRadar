from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import queue
import re
import subprocess
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import config, db, sync
else:
    from . import config, db, sync

log = logging.getLogger("safelens.server")

_APP_CLIENTS: set[queue.Queue] = set()
_APP_CLIENTS_LOCK = threading.Lock()


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sync_state: dict[str, Any] = {
            "running": False,
            "phase": "idle",
            "message": "等待检查",
            "done": 0,
            "total": 0,
            "new": 0,
            "current": "",
            "status": "",
            "started_at": "",
            "finished_at": "",
            "run_id": None,
            "log": [],
            "errors": [],
        }
        self._scheduler_cache: tuple[float, dict[str, Any]] | None = None
        self.cancel_event = threading.Event()

    def start_sync(self, trigger: str = "manual", journal_ids: list[int] | None = None) -> dict[str, Any]:
        with self.lock:
            if self.sync_state.get("running"):
                raise ApiError(409, "已有同步任务正在运行")
            self.cancel_event = threading.Event()
            self.sync_state = {
                "running": True,
                "phase": "starting",
                "message": "正在启动同步…",
                "done": 0,
                "total": 0,
                "new": 0,
                "current": "",
                "status": "running",
                "started_at": db.now_iso(),
                "finished_at": "",
                "run_id": None,
                "log": [{"time": db.now_iso()[11:19], "message": "正在启动同步…", "phase": "starting"}],
                "errors": [],
            }
            state = dict(self.sync_state)

        thread = threading.Thread(target=self._run_sync, args=(trigger, journal_ids, self.cancel_event), daemon=True)
        thread.start()
        return state

    def cancel_sync(self) -> bool:
        with self.lock:
            if not self.sync_state.get("running"):
                return False
            self.cancel_event.set()
            self.sync_state["message"] = "正在中断检查，当前期刊完成后停止…"
            return True

    def _run_sync(self, trigger: str, journal_ids: list[int] | None, cancel_event: threading.Event) -> None:
        def progress(payload: dict[str, Any]) -> None:
            with self.lock:
                for key in ("phase", "message", "done", "total", "new", "current",
                            "status", "started_at", "run_id"):
                    if key in payload and payload[key] is not None:
                        self.sync_state[key] = payload[key]
                if payload.get("running") is False:
                    self.sync_state["running"] = False
                message = payload.get("message")
                if message:
                    logs = self.sync_state.setdefault("log", [])
                    if not logs or logs[-1].get("message") != message:
                        logs.append({"time": db.now_iso()[11:19], "message": message,
                                     "phase": payload.get("phase", "")})
                        del logs[:-40]

        try:
            summary = sync.sync_all(trigger=trigger, journal_ids=journal_ids, progress=progress, cancel_event=cancel_event)
            with self.lock:
                final_status = summary.get("status", "done")
                self.sync_state.update({
                    "running": False,
                    "status": final_status,
                    "message": ("检查已中断，已完成的条目已保存" if final_status == "cancelled"
                                else f"检查完成：新增收录 {summary.get('new_count', 0)} 篇，更新 {summary.get('updated_count', 0)} 篇"),
                    "new": summary.get("new_count", 0),
                    "finished_at": summary.get("finished_at", db.now_iso()),
                    "errors": summary.get("errors", []),
                    "summary": summary,
                })
                self.sync_state.setdefault("log", []).append(
                    {"time": db.now_iso()[11:19], "message": self.sync_state["message"], "phase": "done"}
                )
        except sync.SyncBusy as exc:
            with self.lock:
                self.sync_state.update({"running": False, "status": "busy", "message": str(exc)})
        except Exception as exc:
            log.exception("同步失败")
            with self.lock:
                self.sync_state.update({
                    "running": False, "status": "failed",
                    "message": f"同步失败：{exc}",
                    "finished_at": db.now_iso(),
                })
                self.sync_state.setdefault("log", []).append(
                    {"time": db.now_iso()[11:19], "message": f"同步失败：{exc}", "phase": "failed"}
                )

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            state = dict(self.sync_state)
            state["log"] = list(state.get("log", []))
        state["last_run"] = db.get_last_run()
        return state

    def scheduler(self, force: bool = False) -> dict[str, Any]:
        if not force and self._scheduler_cache and time.time() - self._scheduler_cache[0] < 30:
            return self._scheduler_cache[1]
        status = query_scheduler()
        self._scheduler_cache = (time.time(), status)
        return status


def _powershell(command: str, timeout: int = 25) -> tuple[int, str, str]:
    prefix = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", prefix + command],
            capture_output=True, timeout=timeout,
        )
        out = proc.stdout.decode("utf-8", "replace").strip()
        err = proc.stderr.decode("utf-8", "replace").strip()
        return proc.returncode, out, err
    except Exception as exc:
        return 1, "", str(exc)


def _extract_json(text: str) -> dict[str, Any] | None:
    for match in re.finditer(r"\{.*\}", text, re.S):
        try:
            return json.loads(match.group(0))
        except ValueError:
            continue
    return None


def query_scheduler() -> dict[str, Any]:
    task = config.TASK_NAME
    cmd = f"""
$n='{task}';
try {{
  $t=Get-ScheduledTask -TaskName $n -ErrorAction Stop;
  $i=Get-ScheduledTaskInfo -TaskName $n -ErrorAction Stop;
  $triggerText='';
  if ($t.Triggers -and $t.Triggers.Count -gt 0) {{ $triggerText=[string]$t.Triggers[0].StartBoundary }}
  [pscustomobject]@{{
    installed=$true;
    state=[string]$t.State;
    next_run=if($i.NextRunTime){{$i.NextRunTime.ToString('yyyy-MM-dd HH:mm')}}else{{''}};
    last_run=if($i.LastRunTime -and $i.LastRunTime.Year -gt 2000){{$i.LastRunTime.ToString('yyyy-MM-dd HH:mm')}}else{{''}};
    last_result=if($i.LastTaskResult -eq 267011){{$null}}else{{$i.LastTaskResult}};
    daily_time=$triggerText
  }} | ConvertTo-Json -Compress
}} catch {{
  [pscustomobject]@{{installed=$false; error=$_.Exception.Message}} | ConvertTo-Json -Compress
}}
"""
    code, out, err = _powershell(cmd)
    payload = _extract_json(out)
    if payload is None:
        return {"installed": False, "error": (err or out or "无法读取计划任务状态")[:300], "task_name": task}
    payload["task_name"] = task
    return payload


def install_scheduler() -> dict[str, Any]:
    script = config.DAILY_TASK_SCRIPT
    if not script.exists():
        return {"ok": False, "error": f"找不到安装脚本：{script}"}
    settings = db.get_settings()
    daily_time = settings.get("daily_time") or "08:00"
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-DailyTime", daily_time],
            capture_output=True, timeout=90,
        )
        out = proc.stdout.decode("utf-8", "replace").strip()
        err = proc.stderr.decode("utf-8", "replace").strip()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    payload = _extract_json(out)
    if proc.returncode != 0:
        return {"ok": False, "error": (payload or {}).get("error") or err or out or "计划任务安装失败"}
    if payload is None:
        return {"ok": True, "message": out or "计划任务已安装", "scheduler": query_scheduler()}
    payload["scheduler"] = query_scheduler()
    return payload


def remove_scheduler() -> dict[str, Any]:
    cmd = f"Unregister-ScheduledTask -TaskName '{config.TASK_NAME}' -Confirm:$false"
    code, out, err = _powershell(cmd)
    if code != 0:
        return {"ok": False, "error": err or out}
    return {"ok": True, "scheduler": query_scheduler()}


def _as_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.lower() in ("1", "true", "yes", "on")


def _query_dict(query: str) -> dict[str, str]:
    parsed = urllib.parse.parse_qs(query, keep_blank_values=False)
    return {k: v[-1] for k, v in parsed.items() if v}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _paper_filters(params: dict[str, Any]) -> dict[str, Any]:
    journal_raw = params.get("journal") or ""
    journal_id = None
    if journal_raw and journal_raw not in ("all", "0"):
        journal_id = _int(journal_raw, 0) or None
    search_field = params.get("field") or "all"
    if search_field not in ("all", "title", "author", "abstract", "doi", "journal", "interests"):
        search_field = "all"
    return {
        "view": params.get("view") or "all",
        "journal_id": journal_id,
        "field": search_field,
        "q": (params.get("q") or "").strip()[:200],
        "topic": (params.get("topic") or "").strip()[:100],
        "date_from": (params.get("from") or "").strip()[:10],
        "date_to": (params.get("to") or "").strip()[:10],
        "date_field": params.get("date_field") or "seen",
        "sort": params.get("sort") or "newest",
        "page": max(1, _int(params.get("page"), 1)),
        "page_size": max(1, _int(params.get("page_size"), 30)),
        "starred": _as_bool(params.get("starred")),
        "later": _as_bool(params.get("later")),
        "pdf": _as_bool(params.get("pdf")),
        "rising": _as_bool(params.get("rising")),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "SafeLens/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> AppState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _headers(self, status: int, content_type: str, length: int, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body), {"Cache-Control": "no-store"})
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200,
                    extra: dict[str, str] | None = None) -> None:
        self._headers(status, content_type, len(body), extra)
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = _int(self.headers.get("Content-Length"), 0)
        if length <= 0:
            return {}
        if length > 2_000_000:
            raise ApiError(413, "请求内容过大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "请求内容不是合法 JSON")
        if not isinstance(payload, dict):
            raise ApiError(400, "请求内容必须是 JSON 对象")
        return payload

    def _send_paper_pdf(self, paper_id: int) -> None:
        paper = db.get_paper(paper_id)
        if not paper:
            raise ApiError(404, "论文不存在")
        # 本地 PDF 库优先：已缓存的文件直接离线提供。
        local_path = db.local_pdf_path(paper_id)
        if local_path.exists():
            data = local_path.read_bytes()
            self._headers(200, "application/pdf", len(data), {
                "Content-Disposition": f'attachment; filename="paper-{paper_id}.pdf"',
                "Cache-Control": "no-store",
            })
            self.wfile.write(data)
            return
        url = (paper.get("pdf_url") or "").strip()
        if not url:
            raise ApiError(404, "该论文暂未提供 PDF")
        variants = [
            (url, None),
            (url, paper.get("url") or url),
            (url.replace("http://", "https://"), None),
        ]
        data = b""
        content_type = ""
        last_error: Exception | None = None
        for target, referer in variants:
            headers = {"User-Agent": config.BROWSER_UA, "Accept": "application/pdf,*/*"}
            hostname = urllib.parse.urlsplit(target).hostname or ""
            if hostname.endswith(".cbpt.cnki.net"):
                headers["web_id"] = hostname.split(".")[0]
            if referer:
                headers["Referer"] = referer
            try:
                request = urllib.request.Request(target, headers=headers)
                with urllib.request.urlopen(request, timeout=25) as response:
                    candidate = response.read(30 * 1024 * 1024)
                    candidate_type = response.headers.get("Content-Type", "application/pdf")
                if candidate and (b"%PDF" in candidate[:1024] or "pdf" in candidate_type.lower()):
                    data, content_type = candidate, candidate_type
                    break
            except Exception as exc:
                last_error = exc
            if not data:
                time.sleep(0.8)
        if not data:
            log.warning("PDF 下载失败 %s: %s", url, last_error)
            raise ApiError(502, "PDF 下载失败，请打开原文页面")
        # 下载成功后缓存到本地 PDF 库，下次离线可用。
        try:
            config.PDF_DIR.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(data)
            db.save_local_pdf(paper_id, len(data))
        except Exception as exc:
            log.warning("PDF 本地缓存失败 %s: %s", paper_id, exc)
        self._headers(200, "application/pdf", len(data), {
            "Content-Disposition": f'attachment; filename="paper-{paper_id}.pdf"',
            "Cache-Control": "no-store",
        })
        self.wfile.write(data)

    def _static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            file_path = config.WEB_DIR / "index.html"
        elif path == "/favicon.ico":
            file_path = config.WEB_DIR / "favicon.svg"
        elif path.startswith("/static/"):
            relative = path[len("/static/"):]
            file_path = (config.WEB_DIR / relative).resolve()
            if config.WEB_DIR.resolve() not in file_path.parents:
                raise ApiError(403, "禁止访问")
        else:
            raise ApiError(404, "页面不存在")
        if not file_path.exists() or not file_path.is_file():
            raise ApiError(404, "页面不存在")
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if file_path.suffix == ".js":
            mime = "text/javascript; charset=utf-8"
        elif file_path.suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif file_path.suffix == ".html":
            mime = "text/html; charset=utf-8"
        elif file_path.suffix == ".svg":
            mime = "image/svg+xml"
        body = file_path.read_bytes()
        extra = {"Cache-Control": "no-store, max-age=0" if file_path.suffix in (".html", ".js", ".css") else "max-age=3600"}
        self._send_bytes(body, mime, extra=extra)

    def do_OPTIONS(self) -> None:
        self._send_bytes(b"", "text/plain", 204)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path.startswith("/api/"):
                self._api_get(parsed.path, _query_dict(parsed.query))
            else:
                self._static(parsed.path)
        except ApiError as exc:
            self._send_json({"error": exc.message}, exc.status)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception:
            log.exception("GET %s 失败", self.path)
            self._send_json({"error": "服务器内部错误"}, 500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            self._api_post(parsed.path, self._read_json())
        except ApiError as exc:
            self._send_json({"error": exc.message}, exc.status)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception:
            log.exception("POST %s 失败", self.path)
            self._send_json({"error": "服务器内部错误"}, 500)

    def do_PATCH(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            self._api_patch(parsed.path, self._read_json())
        except ApiError as exc:
            self._send_json({"error": exc.message}, exc.status)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception:
            log.exception("PATCH %s 失败", self.path)
            self._send_json({"error": "服务器内部错误"}, 500)

    def do_PUT(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path != "/api/settings":
                raise ApiError(404, "接口不存在")
            payload = self._read_json()
            saved = db.save_settings(payload)
            self.state._scheduler_cache = None
            self._send_json({"ok": True, "settings": saved})
        except ApiError as exc:
            self._send_json({"error": exc.message}, exc.status)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception:
            log.exception("PUT %s 失败", self.path)
            self._send_json({"error": "服务器内部错误"}, 500)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            match = re.match(r"^/api/journals/(\d+)$", parsed.path)
            if not match:
                raise ApiError(404, "接口不存在")
            ok = db.delete_journal(int(match.group(1)))
            if not ok:
                raise ApiError(400, "预设期刊不能删除，可以停用")
            self._send_json({"ok": True})
        except ApiError as exc:
            self._send_json({"error": exc.message}, exc.status)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception:
            log.exception("DELETE %s 失败", self.path)
            self._send_json({"error": "服务器内部错误"}, 500)

    def _app_events(self) -> None:
        client: queue.Queue[str] = queue.Queue()
        with _APP_CLIENTS_LOCK:
            _APP_CLIENTS.add(client)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = client.get(timeout=15)
                except queue.Empty:
                    message = ": ping\n\n"
                self.wfile.write(message.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            with _APP_CLIENTS_LOCK:
                _APP_CLIENTS.discard(client)

    def _api_get(self, path: str, params: dict[str, str]) -> None:
        if path == "/api/app/events":
            self._app_events()
            return
        if path == "/api/app/state":
            with _APP_CLIENTS_LOCK:
                clients = len(_APP_CLIENTS)
            self._send_json({"clients": clients})
            return
        if path == "/api/health":
            self._send_json({"ok": True, "app": config.APP_NAME, "version": config.VERSION,
                             "time": db.now_iso()})
            return
        if path == "/api/bootstrap":
            self._send_json({
                "app": {"name": config.APP_NAME, "title": config.APP_TITLE, "version": config.VERSION,
                        "timezone": config.TIMEZONE_LABEL},
                "summary": db.get_summary(),
                "journals": db.list_journals(),
                "topics": db.topic_counts(limit=40),
                "settings": db.get_settings(),
                "scheduler": self.state.scheduler(),
                "sync_state": self.state.snapshot(),
                "server_time": db.now_iso(),
            })
            return
        if path == "/api/summary":
            self._send_json({"summary": db.get_summary(), "topics": db.topic_counts(limit=40)})
            return
        if path == "/api/papers/ids":
            filters = _paper_filters(params)
            ids = db.list_paper_ids(
                view=filters["view"], journal_id=filters["journal_id"], q=filters["q"],
                topic=filters["topic"], date_from=filters["date_from"], date_to=filters["date_to"],
                date_field=filters["date_field"], starred=filters["starred"], later=filters["later"],
                field=filters["field"], pdf=filters["pdf"], rising=filters["rising"],
            )
            self._send_json({"ids": ids, "total": len(ids)})
            return
        if path == "/api/papers":
            filters = _paper_filters(params)
            if filters["page_size"] > 100:
                filters["page_size"] = 100
            self._send_json(db.list_papers(**filters))
            return
        match = re.match(r"^/api/papers/(\d+)/pdf$", path)
        if match:
            self._send_paper_pdf(int(match.group(1)))
            return
        match = re.match(r"^/api/papers/(\d+)$", path)
        if match:
            paper = db.get_paper(int(match.group(1)))
            if not paper:
                raise ApiError(404, "论文不存在")
            self._send_json({"paper": paper})
            return
        match = re.match(r"^/api/papers/(\d+)/related$", path)
        if match:
            paper_id = int(match.group(1))
            if not db.get_paper(paper_id):
                raise ApiError(404, "论文不存在")
            self._send_json({"items": db.related_papers(paper_id)})
            return
        if path == "/api/journals":
            self._send_json({"journals": db.list_journals(), "scheduler": self.state.scheduler()})
            return
        if path == "/api/topics":
            self._send_json({"topics": db.topic_counts(limit=40)})
            return
        if path == "/api/sync/status":
            self._send_json({"state": self.state.snapshot(), "last_run": db.get_last_run()})
            return
        if path == "/api/sync/history":
            self._send_json({"runs": db.get_sync_history(_int(params.get("limit"), 12))})
            return
        if path == "/api/local-pdfs":
            stats = db.local_pdf_stats()
            removed = db.prune_missing_local_pdfs()
            if removed:
                stats = db.local_pdf_stats()
            self._send_json({"stats": stats, "pruned": removed})
            return
        if path == "/api/settings":
            self._send_json({"settings": db.get_settings(), "scheduler": self.state.scheduler(force=True)})
            return
        if path == "/api/scheduler":
            self._send_json({"scheduler": self.state.scheduler(force=True)})
            return
        if path == "/api/digest":
            scope = params.get("scope") or "today"
            if scope not in ("today", "week", "latest"):
                scope = "today"
            focus = params.get("focus") or ""
            if focus != "interests":
                focus = ""
            self._send_json(db.get_digest(scope, focus=focus))
            return
        if path == "/api/export":
            fmt = (params.get("format") or "csv").lower()
            if fmt not in ("csv", "bibtex", "markdown", "ris", "json"):
                raise ApiError(400, "不支持的导出格式")
            filters = _paper_filters(params)
            filters.update({"page": 1, "page_size": 1000, "max_page_size": 5000})
            items: list[dict[str, Any]] = []
            selected_ids = params.get("ids")
            if selected_ids is not None:
                try:
                    ids = list(dict.fromkeys(int(value) for value in selected_ids.split(",") if value.strip()))
                except ValueError:
                    raise ApiError(400, "ids 必须是逗号分隔的论文编号")
                if not ids or len(ids) > 5000:
                    raise ApiError(400, "请选择 1 至 5000 篇论文")
                items = [paper for pid in ids if (paper := db.get_paper(pid))]
            page = 1
            while selected_ids is None and len(items) < 5000:
                filters["page"] = page
                result = db.list_papers(**filters)
                items.extend(result["items"])
                if page >= result["pages"]:
                    break
                page += 1
            content, filename, ctype = db.export_papers(items, fmt)
            date_tag = db.today_str().replace("-", "")
            filename = filename.replace("export", f"export-{date_tag}")
            self._send_bytes(
                content.encode("utf-8"), ctype,
                extra={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return
        raise ApiError(404, "接口不存在")

    def _api_post(self, path: str, payload: dict[str, Any]) -> None:
        match = re.match(r"^/api/papers/(\d+)/audit$", path)
        if match:
            paper_id = int(match.group(1))
            if not db.get_paper(paper_id):
                raise ApiError(404, "论文不存在")
            paper = sync.audit_paper(paper_id)
            self._send_json({"ok": True, "paper": paper})
            return
        if path == "/api/app/refresh":
            with _APP_CLIENTS_LOCK:
                clients = list(_APP_CLIENTS)
            for client in clients:
                client.put("event: reload\ndata: refresh\n\n")
            self._send_json({"ok": True, "clients": len(clients)})
            return
        if path == "/api/sync":
            journal_id = payload.get("journal_id")
            ids = [int(journal_id)] if journal_id else None
            state = self.state.start_sync("manual", ids)
            self._send_json({"ok": True, "state": state}, 202)
            return
        if path == "/api/sync/cancel":
            accepted = self.state.cancel_sync()
            self._send_json({"ok": accepted, "message": "正在中断检查" if accepted else "当前没有正在运行的检查"})
            return
        match = re.match(r"^/api/journals/(\d+)/sync$", path)
        if match:
            state = self.state.start_sync("manual", [int(match.group(1))])
            self._send_json({"ok": True, "state": state}, 202)
            return
        if path == "/api/papers/bulk":
            ids = payload.get("ids") or []
            action = str(payload.get("action") or "")
            if not isinstance(ids, list):
                raise ApiError(400, "ids 必须是数组")
            try:
                count = db.bulk_update(ids, action)
            except ValueError as exc:
                raise ApiError(400, str(exc))
            self._send_json({"ok": True, "updated": count})
            return
        if path == "/api/journals":
            try:
                journal = db.add_journal(payload)
            except ValueError as exc:
                raise ApiError(400, str(exc))
            self._send_json({"ok": True, "journal": journal}, 201)
            return
        if path == "/api/scheduler/install":
            result = install_scheduler()
            self.state._scheduler_cache = None
            self._send_json(result)
            return
        if path == "/api/scheduler/remove":
            result = remove_scheduler()
            self.state._scheduler_cache = None
            self._send_json(result)
            return
        raise ApiError(404, "接口不存在")

    def _api_patch(self, path: str, payload: dict[str, Any]) -> None:
        match = re.match(r"^/api/papers/(\d+)$", path)
        if match:
            paper = db.update_paper(int(match.group(1)), payload)
            if not paper:
                raise ApiError(404, "论文不存在")
            self._send_json({"ok": True, "paper": paper})
            return
        match = re.match(r"^/api/journals/(\d+)$", path)
        if match:
            try:
                journal = db.update_journal(int(match.group(1)), payload)
            except ValueError as exc:
                raise ApiError(400, str(exc))
            if not journal:
                raise ApiError(404, "期刊不存在")
            self._send_json({"ok": True, "journal": journal})
            return
        raise ApiError(404, "接口不存在")


def _is_our_server(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("app") == config.APP_NAME
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="SafeLens 安全科学论文雷达")
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    config.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_DIR / "server.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    db.init_db()
    if _is_our_server(args.port):
        url = f"http://127.0.0.1:{args.port}"
        print(f"SafeLens 已在运行：{url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    try:
        server = ThreadingHTTPServer((config.HOST, args.port), Handler)
    except OSError as exc:
        print(f"无法监听端口 {args.port}：{exc}")
        raise SystemExit(1)
    server.daemon_threads = True
    server.state = AppState()  # type: ignore[attr-defined]
    url = f"http://127.0.0.1:{args.port}"
    print(f"SafeLens 已启动：{url}")
    print("按 Ctrl+C 停止服务。")
    if args.open and not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

