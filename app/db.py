from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterable

from . import config

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    issn TEXT NOT NULL DEFAULT '',
    eissn TEXT NOT NULL DEFAULT '',
    homepage_url TEXT NOT NULL DEFAULT '',
    rss_url TEXT NOT NULL DEFAULT '',
    feed_type TEXT NOT NULL DEFAULT 'generic',
    crossref_issn TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#5B6B5A',
    enabled INTEGER NOT NULL DEFAULT 1,
    is_preset INTEGER NOT NULL DEFAULT 0,
    baseline_done INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 100,
    last_sync_at TEXT,
    last_sync_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    doi TEXT,
    pii TEXT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',
    journal_id INTEGER NOT NULL REFERENCES journals(id) ON DELETE CASCADE,
    published_date TEXT,
    published_precision TEXT NOT NULL DEFAULT 'day',
    online_date TEXT,
    volume TEXT NOT NULL DEFAULT '',
    issue TEXT NOT NULL DEFAULT '',
    pages TEXT NOT NULL DEFAULT '',
    section TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    abstract_en TEXT NOT NULL DEFAULT '',
    title_en TEXT NOT NULL DEFAULT '',
    authors_en TEXT NOT NULL DEFAULT '[]',
    affiliations TEXT NOT NULL DEFAULT '',
    funding TEXT NOT NULL DEFAULT '',
    keywords_en TEXT NOT NULL DEFAULT '[]',
    url TEXT NOT NULL DEFAULT '',
    doi_url TEXT NOT NULL DEFAULT '',
    pdf_url TEXT NOT NULL DEFAULT '',
    preview_url TEXT NOT NULL DEFAULT '',
    citation_text TEXT NOT NULL DEFAULT '',
    citation_text_en TEXT NOT NULL DEFAULT '',
    received_date TEXT NOT NULL DEFAULT '',
    revised_date TEXT NOT NULL DEFAULT '',
    accepted_date TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'rss',
    source_label TEXT NOT NULL DEFAULT '',
    topics TEXT NOT NULL DEFAULT '[]',
    keywords TEXT NOT NULL DEFAULT '[]',
    user_tags TEXT NOT NULL DEFAULT '[]',
    citation_count INTEGER NOT NULL DEFAULT 0,
    citation_prev INTEGER NOT NULL DEFAULT 0,
    citation_rising INTEGER NOT NULL DEFAULT 0,
    open_access INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_new INTEGER NOT NULL DEFAULT 1,
    read_at TEXT,
    starred INTEGER NOT NULL DEFAULT 0,
    read_later INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_journal ON papers(journal_id);
CREATE INDEX IF NOT EXISTS idx_papers_seen ON papers(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_papers_read ON papers(read_at);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);

CREATE TABLE IF NOT EXISTS local_pdfs (
    paper_id INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    size INTEGER NOT NULL DEFAULT 0,
    saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL DEFAULT 'manual',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    journals_total INTEGER NOT NULL DEFAULT 0,
    journals_ok INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


@contextmanager
def db():
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    config.ensure_dirs()
    with db() as conn:
        conn.executescript(SCHEMA)
        # 轻量迁移，保证旧数据库也能继续使用。
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
        for name, ddl in {
            "source_metadata": "ALTER TABLE papers ADD COLUMN source_metadata TEXT NOT NULL DEFAULT '{}'",
            "metadata_audit": "ALTER TABLE papers ADD COLUMN metadata_audit TEXT NOT NULL DEFAULT '{}'",
            "user_tags": "ALTER TABLE papers ADD COLUMN user_tags TEXT NOT NULL DEFAULT '[]'",
            "published_precision": "ALTER TABLE papers ADD COLUMN published_precision TEXT NOT NULL DEFAULT 'day'",
            "keywords": "ALTER TABLE papers ADD COLUMN keywords TEXT NOT NULL DEFAULT '[]'",
            "abstract_en": "ALTER TABLE papers ADD COLUMN abstract_en TEXT NOT NULL DEFAULT ''",
            "pdf_url": "ALTER TABLE papers ADD COLUMN pdf_url TEXT NOT NULL DEFAULT ''",
            "preview_url": "ALTER TABLE papers ADD COLUMN preview_url TEXT NOT NULL DEFAULT ''",
            "citation_text": "ALTER TABLE papers ADD COLUMN citation_text TEXT NOT NULL DEFAULT ''",
            "citation_text_en": "ALTER TABLE papers ADD COLUMN citation_text_en TEXT NOT NULL DEFAULT ''",
            "title_en": "ALTER TABLE papers ADD COLUMN title_en TEXT NOT NULL DEFAULT ''",
            "authors_en": "ALTER TABLE papers ADD COLUMN authors_en TEXT NOT NULL DEFAULT '[]'",
            "affiliations": "ALTER TABLE papers ADD COLUMN affiliations TEXT NOT NULL DEFAULT ''",
            "funding": "ALTER TABLE papers ADD COLUMN funding TEXT NOT NULL DEFAULT ''",
            "keywords_en": "ALTER TABLE papers ADD COLUMN keywords_en TEXT NOT NULL DEFAULT '[]'",
            "received_date": "ALTER TABLE papers ADD COLUMN received_date TEXT NOT NULL DEFAULT ''",
            "revised_date": "ALTER TABLE papers ADD COLUMN revised_date TEXT NOT NULL DEFAULT ''",
            "accepted_date": "ALTER TABLE papers ADD COLUMN accepted_date TEXT NOT NULL DEFAULT ''",
            "citation_prev": "ALTER TABLE papers ADD COLUMN citation_prev INTEGER NOT NULL DEFAULT 0",
            "citation_rising": "ALTER TABLE papers ADD COLUMN citation_rising INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in columns:
                conn.execute(ddl)
        seed_journals(conn)
        seed_settings(conn)
        cutoff = (datetime.now() - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "UPDATE sync_runs SET status='failed', finished_at=?, error_count=error_count+1, "
            "detail=? WHERE status='running' AND started_at<?",
            (now_iso(), json.dumps({"errors": [{"journal": "", "error": "同步进程超时或被终止"}]}, ensure_ascii=False), cutoff),
        )


def seed_journals(conn: sqlite3.Connection) -> None:
    created = now_iso()
    for j in config.JOURNALS:
        conn.execute(
            """
            INSERT INTO journals
              (slug, name, short_name, publisher, issn, eissn, homepage_url, rss_url,
               feed_type, crossref_issn, color, enabled, is_preset, baseline_done,
               sort_order, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
              name=excluded.name,
              short_name=excluded.short_name,
              publisher=excluded.publisher,
              issn=excluded.issn,
              eissn=excluded.eissn,
              homepage_url=excluded.homepage_url,
              rss_url=CASE WHEN journals.rss_url='' THEN excluded.rss_url ELSE journals.rss_url END,
              feed_type=excluded.feed_type,
              crossref_issn=excluded.crossref_issn,
              color=excluded.color,
              is_preset=1,
              sort_order=excluded.sort_order
            """,
            (
                j["slug"], j["name"], j["short_name"], j["publisher"], j["issn"],
                j["eissn"], j["homepage_url"], j["rss_url"], j["feed_type"],
                j["crossref_issn"], j["color"], 1, 1, 0, j["sort_order"], created,
            ),
        )


def seed_settings(conn: sqlite3.Connection) -> None:
    for key, value in config.DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def get_settings() -> dict[str, Any]:
    result = dict(config.DEFAULT_SETTINGS)
    with db() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            result[row["key"]] = _json_load(row["value"], row["value"])
    return result


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    allowed = set(config.DEFAULT_SETTINGS)
    with db() as conn:
        for key, value in values.items():
            if key not in allowed:
                continue
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
    return get_settings()


def watched_authors() -> list[str]:
    """用户追踪的作者/机构名，来自设置。"""
    raw = get_settings().get("watched_authors") or []
    return [str(value).strip() for value in raw if str(value).strip()][:30]


def author_watch_expression() -> tuple[str, list[Any]]:
    """匹配论文作者字段中包含任一关注作者的 SQL 片段与参数。"""
    clauses = []
    params: list[Any] = []
    for name in watched_authors():
        like = f"%{name}%"
        clauses.append("(p.authors LIKE ? OR p.authors_en LIKE ?)")
        params.extend([like, like])
    expression = "(" + " OR ".join(clauses) + ")" if clauses else "0"
    return expression, params


def focus_expression() -> tuple[str, list[Any]]:
    """研究偏好 + 关注作者 的组合匹配表达式，用于“我的关注”类视图。"""
    clauses = []
    params: list[Any] = []
    interests = [str(value).strip() for value in (get_settings().get("research_interests") or []) if str(value).strip()][:20]
    for term in interests:
        like = f"%{term}%"
        clauses.append("(p.title LIKE ? OR p.abstract LIKE ? OR p.keywords LIKE ? OR p.topics LIKE ?)")
        params.extend([like, like, like, like])
    author_expr, author_params = author_watch_expression()
    clauses.append(author_expr)
    params.extend(author_params)
    expression = "(" + " OR ".join(clauses) + ")" if clauses else "0"
    return expression, params


def _decode_paper(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["source_metadata"] = _json_load(item.get("source_metadata"), {})
    item["metadata_audit"] = _json_load(item.get("metadata_audit"), {})
    item["authors"] = _json_load(item.get("authors"), [])
    item["authors_en"] = _json_load(item.get("authors_en"), [])
    item["topics"] = _json_load(item.get("topics"), [])
    item["keywords"] = _json_load(item.get("keywords"), [])
    item["keywords_en"] = _json_load(item.get("keywords_en"), [])
    item["user_tags"] = _json_load(item.get("user_tags"), [])
    item["starred"] = bool(item.get("starred"))
    item["read_later"] = bool(item.get("read_later"))
    item["archived"] = bool(item.get("archived"))
    item["open_access"] = bool(item.get("open_access"))
    item["is_new"] = bool(item.get("is_new"))
    item["is_read"] = bool(item.get("read_at"))
    item["citation_rising"] = bool(item.get("citation_rising"))
    item["has_local_pdf"] = bool(item.get("has_local_pdf"))
    return item


def upsert_paper(data: dict[str, Any], is_baseline: bool = False) -> tuple[int, bool]:
    """插入或更新一篇论文。返回 (id, 是否新插入)。"""
    now = now_iso()
    fingerprint = data["fingerprint"]
    doi = (data.get("doi") or "").strip().lower() or None
    with db() as conn:
        row = conn.execute("SELECT id FROM papers WHERE fingerprint=?", (fingerprint,)).fetchone()
        if row is None and doi:
            row = conn.execute("SELECT id FROM papers WHERE doi=?", (doi,)).fetchone()
        if row is None:
            # Some Chinese journals first expose a preprint without DOI, then the final issue.
            row = conn.execute(
                "SELECT id FROM papers WHERE journal_id=? AND lower(trim(title))=lower(trim(?)) LIMIT 1",
                (data["journal_id"], data["title"]),
            ).fetchone()
        if row is not None:
            pid = row["id"]
            current = conn.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
            updates: dict[str, Any] = {}
            # Upgrade a preprint record when the final DOI/issue becomes available.
            if doi and not current["doi"]:
                for key in ("doi", "pii", "published_date", "online_date", "volume", "issue",
                            "pages", "section", "url", "doi_url", "pdf_url", "preview_url",
                            "citation_text", "fingerprint"):
                    if data.get(key):
                        updates[key] = data[key]
            for key in ("doi", "pii", "published_date", "online_date", "volume", "issue", "pages",
                        "section", "url", "doi_url", "pdf_url", "preview_url", "citation_text",
                        "citation_text_en", "title_en", "affiliations", "funding", "abstract_en",
                        "received_date", "revised_date", "accepted_date"):
                if data.get(key) and not current[key]:
                    updates[key] = data[key]
            if data.get("authors_en") and not _json_load(current["authors_en"], []):
                updates["authors_en"] = json.dumps(data["authors_en"], ensure_ascii=False)
            if data.get("keywords_en") and not _json_load(current["keywords_en"], []):
                updates["keywords_en"] = json.dumps(data["keywords_en"], ensure_ascii=False)
            if data.get("abstract") and len(data["abstract"]) > len(current["abstract"] or ""):
                updates["abstract"] = data["abstract"]
            if data.get("authors") and not _json_load(current["authors"], []):
                updates["authors"] = json.dumps(data["authors"], ensure_ascii=False)
            if data.get("topics"):
                updates["topics"] = json.dumps(data["topics"], ensure_ascii=False)
            if data.get("keywords"):
                updates["keywords"] = json.dumps(data["keywords"], ensure_ascii=False)
            if data.get("citation_count", 0) > (current["citation_count"] or 0):
                updates["citation_count"] = data["citation_count"]
            if data.get("open_access"):
                updates["open_access"] = 1
            if data.get("source_label"):
                updates["source_label"] = data["source_label"]
            if updates:
                audit = _json_load(current["metadata_audit"], {})
                evidence_changed = any(current[k] != v for k, v in updates.items()
                                       if k not in {"topics", "source_label", "citation_count", "open_access"})
                if audit and evidence_changed:
                    audit["status"] = "stale"
                    audit["acceptance"] = {"status": "pending", "reasons": ["元数据已更新，需要重新进行合格检验"]}
                    audit["stale_reason"] = "入库元数据已更新，请重新核查"
                    updates["metadata_audit"] = json.dumps(audit, ensure_ascii=False)
                updates["updated_at"] = now
                assignments = ", ".join(f"{k}=?" for k in updates)
                conn.execute(f"UPDATE papers SET {assignments}, last_seen_at=? WHERE id=?", (*updates.values(), now, pid))
            else:
                conn.execute("UPDATE papers SET last_seen_at=? WHERE id=?", (now, pid))
            return pid, False

        cur = conn.execute(
            """
            INSERT INTO papers
              (fingerprint, doi, pii, title, authors, journal_id, published_date,
               published_precision, online_date, volume, issue, pages, section,
               abstract, abstract_en, title_en, authors_en, affiliations, funding, keywords_en,
               url, doi_url, pdf_url, preview_url, citation_text, citation_text_en,
               received_date, revised_date, accepted_date, source, source_label, topics, keywords, user_tags,
               citation_count, open_access, first_seen_at, last_seen_at, is_new,
               created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fingerprint, doi, data.get("pii") or None, data["title"],
                json.dumps(data.get("authors") or [], ensure_ascii=False),
                data["journal_id"], data.get("published_date") or None,
                data.get("published_precision") or "day", data.get("online_date") or None,
                data.get("volume") or "", data.get("issue") or "", data.get("pages") or "",
                data.get("section") or "", data.get("abstract") or "", data.get("abstract_en") or "",
                data.get("title_en") or "", json.dumps(data.get("authors_en") or [], ensure_ascii=False),
                data.get("affiliations") or "", data.get("funding") or "",
                json.dumps(data.get("keywords_en") or [], ensure_ascii=False),
                data.get("url") or "", data.get("doi_url") or "", data.get("pdf_url") or "",
                data.get("preview_url") or "", data.get("citation_text") or "", data.get("citation_text_en") or "",
                data.get("received_date") or "", data.get("revised_date") or "",
                data.get("accepted_date") or "", data.get("source") or "rss",
                data.get("source_label") or "",
                json.dumps(data.get("topics") or [], ensure_ascii=False),
                json.dumps(data.get("keywords") or [], ensure_ascii=False), "[]",
                int(data.get("citation_count") or 0), 1 if data.get("open_access") else 0,
                now, now, 0 if is_baseline else 1, now, now,
            ),
        )
        return int(cur.lastrowid), True


def mark_journal_sync(journal_id: int, status: str, error: str | None, baseline: bool = True) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE journals SET last_sync_at=?, last_sync_status=?, last_error=?, "
            "baseline_done=CASE WHEN ? THEN 1 ELSE baseline_done END WHERE id=?",
            (now_iso(), status, (error or "")[:1000], 1 if baseline else 0, journal_id),
        )


def get_journal(journal_id: int) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM journals WHERE id=?", (journal_id,)).fetchone()
        return dict(row) if row else None


def get_enabled_journals() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM journals WHERE enabled=1 ORDER BY sort_order, id").fetchall()
        return [dict(r) for r in rows]


def list_journals() -> list[dict[str, Any]]:
    today = today_str()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT j.*,
              COUNT(p.id) AS paper_count,
              COALESCE(SUM(CASE WHEN p.read_at IS NULL AND p.archived=0 THEN 1 ELSE 0 END),0) AS unread_count,
              COALESCE(SUM(CASE WHEN p.is_new=1 AND date(p.first_seen_at)=? THEN 1 ELSE 0 END),0) AS today_new,
              MAX(p.first_seen_at) AS latest_seen,
              MAX(p.published_date) AS latest_published
            FROM journals j
            LEFT JOIN papers p ON p.journal_id=j.id
            GROUP BY j.id
            ORDER BY j.sort_order, j.id
            """,
            (today,),
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            item["enabled"] = bool(item["enabled"])
            item["is_preset"] = bool(item["is_preset"])
            item["baseline_done"] = bool(item["baseline_done"])
            result.append(item)
        return result


def get_summary() -> dict[str, Any]:
    today = today_str()
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
    focus_where, interest_params = focus_expression()
    author_where, author_params = author_watch_expression()
    with db() as conn:
        totals = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              COALESCE(SUM(CASE WHEN read_at IS NULL AND archived=0 THEN 1 ELSE 0 END),0) AS unread,
              COALESCE(SUM(CASE WHEN starred=1 AND archived=0 THEN 1 ELSE 0 END),0) AS starred,
              COALESCE(SUM(CASE WHEN read_later=1 AND archived=0 THEN 1 ELSE 0 END),0) AS later,
              COALESCE(SUM(CASE WHEN archived=1 THEN 1 ELSE 0 END),0) AS archived,
              COALESCE(SUM(CASE WHEN is_new=1 AND date(first_seen_at)=? AND archived=0 THEN 1 ELSE 0 END),0) AS today_new,
              COALESCE(SUM(CASE WHEN is_new=1 AND first_seen_at>=? AND archived=0 THEN 1 ELSE 0 END),0) AS week_new,
              COALESCE(SUM(CASE WHEN date(first_seen_at)=? THEN 1 ELSE 0 END),0) AS today_added
            FROM papers
            """,
            (today, week_ago, today),
        ).fetchone()
        journals = conn.execute("SELECT COUNT(*) AS c FROM journals WHERE enabled=1").fetchone()["c"]
        watch_new = 0
        if author_where != "0":
            watch_new = conn.execute(
                f"SELECT COUNT(*) AS c FROM papers p WHERE p.archived=0 "
                f"AND p.is_new=1 AND date(p.first_seen_at)=? AND {author_where}",
                (today, *author_params),
            ).fetchone()["c"]
        local_pdfs = conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(size),0) AS b FROM local_pdfs").fetchone()
        rising_count = conn.execute(
            "SELECT COUNT(*) AS c FROM papers WHERE citation_rising=1 AND archived=0"
        ).fetchone()["c"]
        last_run = conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_ok = conn.execute(
            "SELECT * FROM sync_runs WHERE status IN ('done','partial') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        interest_items = []
        if focus_where != "0":
            interest_items = conn.execute(
                f"""
                SELECT p.*, j.name AS journal_name, j.short_name AS journal_short,
                       j.color AS journal_color, j.publisher AS journal_publisher,
                       1 AS interest_match
                FROM papers p JOIN journals j ON j.id=p.journal_id
                WHERE p.archived=0 AND {focus_where}
                  AND p.is_new=1 AND date(p.first_seen_at)=?
                ORDER BY p.first_seen_at DESC, p.id DESC
                LIMIT 8
                """,
                (*interest_params, today),
            ).fetchall()
        latest_rows = conn.execute(
            """
            SELECT * FROM (
              SELECT p.*, j.name AS journal_name, j.short_name AS journal_short,
                     j.color AS journal_color, j.publisher AS journal_publisher,
                     0 AS interest_match,
                     ROW_NUMBER() OVER (PARTITION BY p.journal_id ORDER BY p.first_seen_at DESC, p.id DESC) AS rn
              FROM papers p JOIN journals j ON j.id=p.journal_id
              WHERE p.archived=0
            ) WHERE rn=1 ORDER BY first_seen_at DESC, id DESC LIMIT 16
            """
        ).fetchall()
        latest = []
        seen_ids: set[int] = set()
        for row in list(interest_items) + list(latest_rows):
            row_id = int(row["id"])
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            latest.append(row)
            if len(latest) >= 8:
                break
        result = dict(totals)
        result["journal_count"] = journals
        result["watch_new"] = watch_new
        result["local_pdf_count"] = local_pdfs["c"]
        result["local_pdf_bytes"] = local_pdfs["b"]
        result["citation_rising_count"] = rising_count
        result["last_run"] = dict(last_run) if last_run else None
        if result["last_run"]:
            result["last_run"]["detail"] = _json_load(result["last_run"].get("detail"), {})
        result["last_sync"] = dict(last_ok) if last_ok else None
        if result["last_sync"]:
            result["last_sync"]["detail"] = _json_load(result["last_sync"].get("detail"), {})
        result["latest"] = [_decode_paper(r) for r in latest]
        result["today"] = today
        return result


def _build_paper_where(view: str, journal_id: int | None, q: str, topic: str,
                       date_from: str, date_to: str, date_field: str,
                       starred: bool | None, later: bool | None,
                       search_field: str = "all", pdf: bool | None = None,
                       rising: bool | None = None) -> tuple[str, list[Any]]:
    where = ["1=1"]
    params: list[Any] = []
    today = today_str()
    if view in ("inbox", "unread"):
        where.append("p.read_at IS NULL AND p.archived=0")
    elif view == "watch":
        author_where, author_params = author_watch_expression()
        where.append(f"p.archived=0 AND {author_where}")
        params.extend(author_params)
    elif view == "today":
        where.append("p.is_new=1 AND date(p.first_seen_at)=? AND p.archived=0")
        params.append(today)
    elif view == "starred":
        where.append("p.starred=1 AND p.archived=0")
    elif view == "later":
        where.append("p.read_later=1 AND p.archived=0")
    elif view == "archive":
        where.append("p.archived=1")
    elif view == "all":
        where.append("p.archived=0")
    if journal_id:
        where.append("p.journal_id=?")
        params.append(journal_id)
    if search_field == "interests":
        focus_where, focus_params = focus_expression()
        where.append(focus_where)
        params.extend(focus_params)
    if q:
        columns = ["p.title", "p.abstract", "p.authors", "p.doi", "j.name"]
        if search_field != "interests":
            columns = {
                "title": ["p.title"],
                "author": ["p.authors"],
                "abstract": ["p.abstract"],
                "doi": ["p.doi"],
                "journal": ["j.name"],
            }.get(search_field, columns)
        for term in [t for t in re.split(r"\s+", q.strip()) if t][:8]:
            like = f"%{term}%"
            where.append("(" + " OR ".join(f"{column} LIKE ?" for column in columns) + ")")
            params.extend([like] * len(columns))
    if topic:
        where.append("EXISTS (SELECT 1 FROM json_each(p.topics) WHERE json_each.value=?)")
        params.append(topic)
    if date_from:
        field = "p.published_date" if date_field == "published" else "date(p.first_seen_at)"
        where.append(f"{field} >= ?")
        params.append(date_from)
    if date_to:
        field = "p.published_date" if date_field == "published" else "date(p.first_seen_at)"
        where.append(f"{field} <= ?")
        params.append(date_to)
    if starred is not None:
        where.append("p.starred=?")
        params.append(1 if starred else 0)
    if later is not None:
        where.append("p.read_later=?")
        params.append(1 if later else 0)
    if pdf is not None:
        where.append("EXISTS (SELECT 1 FROM local_pdfs lp WHERE lp.paper_id=p.id)" if pdf
                     else "NOT EXISTS (SELECT 1 FROM local_pdfs lp WHERE lp.paper_id=p.id)")
    if rising is not None:
        where.append("p.citation_rising=?")
        params.append(1 if rising else 0)
    return " AND ".join(where), params


def list_papers(*, view: str = "all", journal_id: int | None = None, q: str = "",
                topic: str = "", date_from: str = "", date_to: str = "",
                date_field: str = "seen", sort: str = "newest",
                page: int = 1, page_size: int = 30, max_page_size: int = 100,
                starred: bool | None = None, later: bool | None = None,
                field: str = "all", pdf: bool | None = None,
                rising: bool | None = None) -> dict[str, Any]:
    where, params = _build_paper_where(view, journal_id, q, topic, date_from, date_to,
                                       date_field, starred, later, field, pdf, rising)
    page = max(1, int(page or 1))
    page_size = min(max(1, int(max_page_size or 100)), max(1, int(page_size or 30)))
    focus_where, focus_params = focus_expression()
    author_where, author_params = author_watch_expression()
    interest_score_sql = ""
    interest_score_params: list[Any] = []
    if sort == "interest":
        expression = f"CASE WHEN {focus_where} THEN 1 ELSE 0 END"
        interest_score_sql = ", " + expression + " AS interest_score"
        interest_score_params.extend(focus_params)
    watch_match_sql = f", {author_where} AS watch_match"
    order_map = {
        "interest": "interest_score DESC, p.first_seen_at DESC, p.id DESC",
        "newest": "p.first_seen_at DESC, p.id DESC",
        "published": "COALESCE(p.published_date,'') DESC, p.id DESC",
        "cited": "p.citation_count DESC, p.first_seen_at DESC",
        "title": "p.title COLLATE NOCASE ASC",
    }
    order = order_map.get(sort, order_map["interest"])
    base = f"""
      FROM papers p JOIN journals j ON j.id=p.journal_id
      LEFT JOIN local_pdfs lp ON lp.paper_id=p.id
      WHERE {where}
    """
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c {base}", params).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT p.*, j.name AS journal_name, j.short_name AS journal_short,
                   j.color AS journal_color, j.publisher AS journal_publisher,
                   j.homepage_url AS journal_homepage,
                   CASE WHEN lp.paper_id IS NOT NULL THEN 1 ELSE 0 END AS has_local_pdf
                   {watch_match_sql}
                   {interest_score_sql}
            {base}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (*author_params, *interest_score_params, *params, page_size, (page - 1) * page_size),
        ).fetchall()
        return {
            "items": [_decode_paper(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }


def list_paper_ids(*, view: str = "all", journal_id: int | None = None, q: str = "",
                   topic: str = "", date_from: str = "", date_to: str = "",
                   date_field: str = "seen", starred: bool | None = None,
                   later: bool | None = None, field: str = "all",
                   pdf: bool | None = None, rising: bool | None = None) -> list[int]:
    where, params = _build_paper_where(view, journal_id, q, topic, date_from, date_to,
                                       date_field, starred, later, field, pdf, rising)
    with db() as conn:
        rows = conn.execute(
            f"SELECT p.id FROM papers p JOIN journals j ON j.id=p.journal_id WHERE {where} ORDER BY p.id",
            params,
        ).fetchall()
    return [int(row["id"]) for row in rows]


def get_paper(paper_id: int) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT p.*, j.name AS journal_name, j.short_name AS journal_short,
                   j.color AS journal_color, j.publisher AS journal_publisher,
                   j.homepage_url AS journal_homepage,
                   CASE WHEN lp.paper_id IS NOT NULL THEN 1 ELSE 0 END AS has_local_pdf
            FROM papers p JOIN journals j ON j.id=p.journal_id
            LEFT JOIN local_pdfs lp ON lp.paper_id=p.id
            WHERE p.id=?
            """,
            (paper_id,),
        ).fetchone()
        return _decode_paper(row) if row else None


def get_paper_by_fingerprint(fingerprint: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM papers WHERE fingerprint=?", (fingerprint,)).fetchone()
        return _decode_paper(row) if row else None


def save_metadata_audit(paper_id: int, result: dict[str, Any]) -> dict[str, Any] | None:
    """Keep source evidence separate; never overwrite conflicting values or reader data."""
    metadata = result.get("metadata") or {}
    audit = dict(result.get("audit") or {})
    arrays = {"authors", "authors_en", "keywords", "keywords_en"}
    allowed = arrays | {"title", "title_en", "abstract", "abstract_en", "affiliations",
                        "funding", "pdf_url", "preview_url", "citation_text", "citation_text_en",
                        "received_date", "revised_date", "accepted_date", "volume", "issue", "pages"}
    with db() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        if row is None:
            return None
        current = _decode_paper(row)
        updates = {}
        for key in allowed:
            value = metadata.get(key)
            if value and not current.get(key):
                updates[key] = json.dumps(value, ensure_ascii=False) if key in arrays else value
        # A failed recheck retains the last successful extraction, but never its verified status.
        if metadata:
            updates["source_metadata"] = json.dumps(metadata, ensure_ascii=False)
        updates["metadata_audit"] = json.dumps(audit, ensure_ascii=False)
        updates["updated_at"] = now_iso()
        assignments = ", ".join(f"{key}=?" for key in updates)
        conn.execute(f"UPDATE papers SET {assignments} WHERE id=?", (*updates.values(), paper_id))
    return get_paper(paper_id)


def metadata_audit_candidates(journal_id: int, limit: int = 3) -> list[int]:
    """Progressively cover old records, prioritizing never-checked records over retries."""
    with db() as conn:
        rows = conn.execute("SELECT id, metadata_audit FROM papers WHERE journal_id=? ORDER BY id", (journal_id,)).fetchall()
    pending = []
    retry = []
    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    for row in rows:
        audit = _json_load(row["metadata_audit"], {})
        if not audit:
            pending.append(row["id"])
        elif audit.get("status") == "stale" or (audit.get("checked_at", "") < cutoff and audit.get("status") in {"failed", "error", "fetch_failed", "unavailable"}):
            retry.append(row["id"])
    return (pending + retry)[:limit]


def update_paper(paper_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"starred", "read_later", "archived", "notes", "user_tags"}
    updates: dict[str, Any] = {}
    for key, value in values.items():
        if key in allowed:
            if key == "user_tags":
                updates[key] = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
            elif key in ("starred", "read_later", "archived"):
                updates[key] = 1 if value else 0
            elif key == "notes":
                updates[key] = str(value)[:5000]
    if "read" in values:
        updates["read_at"] = now_iso() if values["read"] else None
    if not updates:
        return get_paper(paper_id)
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{k}=?" for k in updates)
    with db() as conn:
        conn.execute(f"UPDATE papers SET {assignments} WHERE id=?", (*updates.values(), paper_id))
    return get_paper(paper_id)


def bulk_update(ids: Iterable[int], action: str) -> int:
    ids = list(dict.fromkeys(int(i) for i in ids))[:5000]
    if not ids:
        return 0
    now = now_iso()
    statements = {
        "read": "UPDATE papers SET read_at=COALESCE(read_at,?), updated_at=? WHERE id IN ({marks})",
        "unread": "UPDATE papers SET read_at=NULL, updated_at=? WHERE id IN ({marks})",
        "star": "UPDATE papers SET starred=1, updated_at=? WHERE id IN ({marks})",
        "unstar": "UPDATE papers SET starred=0, updated_at=? WHERE id IN ({marks})",
        "later": "UPDATE papers SET read_later=1, updated_at=? WHERE id IN ({marks})",
        "unlater": "UPDATE papers SET read_later=0, updated_at=? WHERE id IN ({marks})",
        "archive": "UPDATE papers SET archived=1, updated_at=? WHERE id IN ({marks})",
        "unarchive": "UPDATE papers SET archived=0, updated_at=? WHERE id IN ({marks})",
        "clear_rising": "UPDATE papers SET citation_rising=0, updated_at=? WHERE id IN ({marks})",
    }
    if action not in statements:
        raise ValueError("未知操作")
    updated = 0
    with db() as conn:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            sql = statements[action].format(marks=marks)
            params = (now, now, *chunk) if action == "read" else (now, *chunk)
            updated += conn.execute(sql, params).rowcount
    return updated


def topic_counts(days: int = 30, limit: int = 10) -> list[dict[str, Any]]:
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    counts: dict[str, int] = {}
    with db() as conn:
        rows = conn.execute(
            "SELECT topics FROM papers WHERE first_seen_at>=? AND archived=0", (start,)
        ).fetchall()
    for row in rows:
        for topic in _json_load(row["topics"], []):
            counts[topic] = counts.get(topic, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [
        {"name": name, "count": count, "color": config.TOPIC_COLORS.get(name, "#5B6B5A")}
        for name, count in ordered
    ]


def retag_all_papers() -> int:
    from .sources import build_topics
    with db() as conn:
        rows = conn.execute("SELECT id, title, abstract, keywords FROM papers").fetchall()
        for row in rows:
            keywords = _json_load(row["keywords"], [])
            topics = build_topics(row["title"], row["abstract"] or "", keywords)
            conn.execute("UPDATE papers SET topics=? WHERE id=?",
                         (json.dumps(topics, ensure_ascii=False), row["id"]))
    return len(rows)


def create_sync_run(trigger: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO sync_runs(trigger, started_at, status) VALUES(?,?,'running')",
            (trigger, now_iso()),
        )
        return int(cur.lastrowid)


def finish_sync_run(run_id: int, *, status: str, journals_total: int, journals_ok: int,
                    new_count: int, updated_count: int, error_count: int,
                    detail: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE sync_runs SET finished_at=?, status=?, journals_total=?, journals_ok=?,
              new_count=?, updated_count=?, error_count=?, detail=? WHERE id=?
            """,
            (now_iso(), status, journals_total, journals_ok, new_count, updated_count,
             error_count, json.dumps(detail, ensure_ascii=False), run_id),
        )


def get_last_run() -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        item = dict(row)
        item["detail"] = _json_load(item.get("detail"), {})
        return item


def get_sync_history(limit: int = 12) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["detail"] = _json_load(item.get("detail"), {})
        result.append(item)
    return result


def get_digest(scope: str = "today", limit: int = 1000, focus: str = "") -> dict[str, Any]:
    today = today_str()
    where = "p.archived=0"
    params: list[Any] = []
    label = "今日新增"
    if scope == "week":
        start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        where += " AND p.first_seen_at>=?"
        params.append(start)
        label = "近 7 天入库"
    elif scope == "latest":
        last = get_last_run()
        if last and last.get("started_at"):
            where += " AND p.first_seen_at>=?"
            params.append(last["started_at"])
        label = "最近一次检查"
    else:
        where += " AND p.is_new=1 AND date(p.first_seen_at)=?"
        params.append(today)
    interests: list[str] = []
    if focus == "interests":
        raw = get_settings().get("research_interests") or []
        interests = [str(value).strip() for value in raw if str(value).strip()][:20] + watched_authors()
        focus_where, focus_params = focus_expression()
        where += f" AND {focus_where}"
        params.extend(focus_params)
        label = "我的关注"
    with db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM papers p JOIN journals j ON j.id=p.journal_id WHERE {where}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT p.*, j.name AS journal_name, j.short_name AS journal_short,
                   j.color AS journal_color, j.sort_order AS journal_order
            FROM papers p JOIN journals j ON j.id=p.journal_id
            WHERE {where}
            ORDER BY journal_order, p.published_date DESC, p.id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    items = [_decode_paper(r) for r in rows]
    groups_map: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item["journal_name"]
        if key not in groups_map:
            groups_map[key] = {
                "journal": key,
                "color": item.get("journal_color"),
                "items": [],
            }
        groups_map[key]["items"].append(item)
    groups = list(groups_map.values())
    markdown = _digest_markdown(today, label, groups)
    return {"scope": scope, "focus": "interests" if focus == "interests" else "", "label": label,
            "date": today, "count": len(items), "total_count": total, "truncated": total > len(items),
            "interests": interests, "groups": groups, "markdown": markdown}


def _digest_markdown(today: str, label: str, groups: list[dict[str, Any]]) -> str:
    lines = [f"# 安全科学论文简报 · {today}", "", f"**{label}**：共 {sum(len(g['items']) for g in groups)} 篇", ""]
    if not groups:
        lines.append("今日暂未发现新论文。")
        return "\n".join(lines)
    for group in groups:
        lines.append(f"## {group['journal']}（{len(group['items'])} 篇）")
        lines.append("")
        for item in group["items"]:
            authors = ", ".join(item.get("authors") or []) or "作者信息暂无"
            date = item.get("published_date") or item.get("online_date") or ""
            lines.append(f"- **{item['title']}**")
            lines.append(f"  - {authors}")
            meta = " · ".join(x for x in [date, f"DOI: {item['doi']}" if item.get("doi") else ""] if x)
            if meta:
                lines.append(f"  - {meta}")
            if item.get("url"):
                lines.append(f"  - [原文]({item['url']})")
            if item.get("keywords"):
                lines.append(f"  - 关键词：{' / '.join(item['keywords'])}")
            abstract = (item.get("abstract") or "").strip()
            if abstract:
                lines.append(f"  - {abstract[:260]}{'…' if len(abstract) > 260 else ''}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def add_journal(data: dict[str, Any]) -> dict[str, Any]:
    import hashlib
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("期刊名称不能为空")
    issn = (data.get("issn") or "").strip()
    rss_url = (data.get("rss_url") or "").strip()
    homepage = (data.get("homepage_url") or "").strip()
    if not rss_url and not issn:
        raise ValueError("请提供 ISSN 或 RSS 地址")
    if rss_url and not re.match(r"^https?://", rss_url):
        raise ValueError("RSS 地址必须以 http:// 或 https:// 开头")
    if homepage and not re.match(r"^https?://", homepage):
        raise ValueError("官网地址必须以 http:// 或 https:// 开头")
    feed_type = "generic" if rss_url else "crossref"
    slug_base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "journal"
    slug = f"{slug_base}-{hashlib.sha1((issn + name).encode()).hexdigest()[:6]}"
    color = data.get("color") or "#5B6B5A"
    with db() as conn:
        existing = conn.execute("SELECT id FROM journals WHERE slug=?", (slug,)).fetchone()
        if existing:
            return dict(conn.execute("SELECT * FROM journals WHERE id=?", (existing["id"],)).fetchone())
        cur = conn.execute(
            """
            INSERT INTO journals
              (slug,name,short_name,publisher,issn,eissn,homepage_url,rss_url,feed_type,
               crossref_issn,color,enabled,is_preset,baseline_done,sort_order,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (slug, name, data.get("short_name") or name[:22], data.get("publisher") or "自定义",
             issn, data.get("eissn") or "", homepage, rss_url, feed_type,
             issn if issn and not rss_url else "", color, 1, 0, 0, 500, now_iso()),
        )
        return dict(conn.execute("SELECT * FROM journals WHERE id=?", (cur.lastrowid,)).fetchone())


def update_journal(journal_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"enabled", "rss_url", "homepage_url", "name", "color", "crossref_issn"}
    updates: dict[str, Any] = {}
    for key, value in values.items():
        if key in allowed:
            if key == "enabled":
                updates[key] = 1 if value else 0
            elif key in ("rss_url", "homepage_url") and value:
                value = str(value).strip()
                if not re.match(r"^https?://", value):
                    raise ValueError("地址必须以 http:// 或 https:// 开头")
                updates[key] = value
            else:
                updates[key] = value
    if not updates:
        return get_journal(journal_id)
    with db() as conn:
        current = conn.execute("SELECT is_preset, feed_type FROM journals WHERE id=?", (journal_id,)).fetchone()
        if not current:
            return None
        if "rss_url" in updates and not current["is_preset"]:
            updates["feed_type"] = "generic" if updates["rss_url"] else "crossref"
        assignments = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE journals SET {assignments} WHERE id=?", (*updates.values(), journal_id))
    return get_journal(journal_id)


def delete_journal(journal_id: int) -> bool:
    with db() as conn:
        row = conn.execute("SELECT is_preset FROM journals WHERE id=?", (journal_id,)).fetchone()
        if not row or row["is_preset"]:
            return False
        conn.execute("DELETE FROM journals WHERE id=?", (journal_id,))
        return True


def export_papers(items: list[dict[str, Any]], fmt: str) -> tuple[str, str, str]:
    import csv
    import io
    if fmt == "json":
        return json.dumps(items, ensure_ascii=False, indent=2), "safelens-export.json", "application/json; charset=utf-8"
    if fmt == "ris":
        entries = []
        for item in items:
            def line(tag, value):
                return f"{tag}  - " + str(value).replace("\r", " ").replace("\n", " ")
            lines = ["TY  - JOUR", line("TI", item.get("title", ""))]
            lines.extend(line("AU", author) for author in item.get("authors") or [])
            for tag, key in (("JO", "journal_name"), ("PY", "published_date"), ("VL", "volume"),
                             ("IS", "issue"), ("DO", "doi"), ("UR", "url"), ("AB", "abstract")):
                if item.get(key):
                    lines.append(line(tag, item[key]))
            pages = str(item.get("pages") or "").strip()
            if pages:
                bounds = re.split(r"\s*(?:--?|[–—])\s*", pages, maxsplit=1)
                lines.append(line("SP", bounds[0]))
                if len(bounds) == 2 and bounds[1]:
                    lines.append(line("EP", bounds[1]))
            lines.extend(line("KW", keyword) for keyword in item.get("keywords") or [])
            lines.extend(["ER  -", ""])
            entries.append("\n".join(lines))
        return "\n".join(entries), "safelens-export.ris", "application/x-research-info-systems; charset=utf-8"
    if fmt == "bibtex":
        escapes = {"\\": r"{\textbackslash{}}", "{": r"\{", "}": r"\}",
                   "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                   "~": r"{\textasciitilde{}}", "^": r"{\textasciicircum{}}"}
        def bib_escape(value):
            return "".join(escapes.get(char, char) for char in str(value)).replace("\r", " ").replace("\n", " ")
        entries = []
        for item in items:
            first_author = (item.get("authors") or ["SafeLens"])[0]
            last = re.sub(r"[^A-Za-z]", "", first_author.split(",")[0].split()[-1]) if first_author else "item"
            year = (item.get("published_date") or item.get("online_date") or "")[:4] or "n.d."
            word = re.sub(r"[^A-Za-z]", "", (item.get("title") or "paper").split()[0]) or "paper"
            key = f"{last or 'item'}{year}{word}{item['id']}"
            fields = {
                "title": item.get("title", ""),
                "author": " and ".join(item.get("authors") or []),
                "journal": item.get("journal_name", ""),
                "year": year,
                "volume": item.get("volume", ""),
                "number": item.get("issue", ""),
                "pages": item.get("pages", ""),
                "doi": item.get("doi") or "",
                "url": item.get("url") or "",
                "abstract": item.get("abstract", ""),
                "keywords": " / ".join(item.get("keywords") or []),
            }
            body = ",\n".join(f"  {k} = {{{bib_escape(v)}}}" for k, v in fields.items() if v)
            entries.append(f"@article{{{key},\n{body}\n}}")
        return "\n\n".join(entries) + "\n", "safelens-export.bib", "application/x-bibtex; charset=utf-8"
    if fmt == "markdown":
        lines = ["# 安全科学论文导出", ""]
        for item in items:
            lines.append(f"## {item['title']}")
            lines.append(f"- 期刊：{item.get('journal_name','')}")
            lines.append(f"- 作者：{', '.join(item.get('authors') or [])}")
            if item.get("doi"):
                lines.append(f"- DOI：{item['doi']}")
            if item.get("url"):
                lines.append(f"- 原文：{item['url']}")
            if item.get("keywords"):
                lines.append(f"- 关键词：{' / '.join(item['keywords'])}")
            lines.append("")
        return "\n".join(lines), "safelens-export.md", "text/markdown; charset=utf-8"
    buf = io.StringIO()
    writer = csv.writer(buf)
    bilingual = [(key, label) for key, label in (("title", "标题"), ("authors", "作者"),
                 ("affiliations", "机构"), ("funding", "基金"), ("abstract", "摘要"), ("keywords", "关键词"))]
    writer.writerow(["标题", "作者", "期刊", "发表日期", "卷", "期", "页码", "DOI", "原文链接", "主题", "关键词", "状态", "收藏", "入库时间"] +
                    [language + label for _, label in bilingual for language in ("中文", "英文")] +
                    ["官网PDF链接", "在线预览链接", "审计状态", "审计时间"])
    for item in items:
        source = item.get("source_metadata") or {}
        def bilingual_value(key, language):
            value = source.get(key + "_" + language) or item.get(key + "_" + language)
            if not value:
                original = source.get(key) or item.get(key) or ""
                original_text = "; ".join(original) if isinstance(original, list) else str(original)
                detected = "zh" if re.search(r"[\u3400-\u9fff]", original_text) else "en"
                value = original if detected == language else ""
            return "; ".join(value) if isinstance(value, list) else value
        audit = item.get("metadata_audit") or {}
        writer.writerow([
            item.get("title", ""), "; ".join(item.get("authors") or []),
            item.get("journal_name", ""), item.get("published_date", ""),
            item.get("volume", ""), item.get("issue", ""), item.get("pages", ""),
            item.get("doi", ""), item.get("url", ""), " / ".join(item.get("topics") or []),
            " / ".join(item.get("keywords") or []),
            "已读" if item.get("read_at") else "未读", "是" if item.get("starred") else "否",
            item.get("first_seen_at", ""),
        ] + [bilingual_value(key, language) for key, _ in bilingual for language in ("zh", "en")] +
            [source.get("pdf_url") or item.get("pdf_url", ""), source.get("preview_url") or item.get("preview_url", ""),
             audit.get("status", "未核查"), audit.get("checked_at", "")])
    return "\ufeff" + buf.getvalue(), "safelens-export.csv", "text/csv; charset=utf-8"


# ---------- 被引数定期刷新 ----------

def papers_for_citation_refresh(limit: int = 3000) -> list[dict[str, Any]]:
    """返回带 DOI 的论文（id, doi, citation_count），按入库顺序分批刷新。"""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, doi, citation_count FROM papers WHERE doi IS NOT NULL AND doi != '' "
            "ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_citation(paper_id: int, new_count: int) -> bool:
    """以 OpenAlex 的权威值更新被引数，并检测“引用上升”。

    上升阈值：增量 >= max(3, 原值的 50%)；原值为 0 时需 >= 8 才提醒，
    避免新入库论文的低基数误报。
    """
    new_count = max(0, int(new_count))
    with db() as conn:
        row = conn.execute("SELECT citation_count FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not row:
            return False
        old = int(row["citation_count"] or 0)
        if new_count == old:
            return False
        delta = new_count - old
        if old == 0:
            rising = 1 if new_count >= 8 else 0
        else:
            rising = 1 if delta >= max(3, old // 2) else 0
        conn.execute(
            "UPDATE papers SET citation_prev=?, citation_count=?, citation_rising=?, updated_at=? WHERE id=?",
            (old, new_count, rising, now_iso(), paper_id),
        )
    return True


def citation_refresh_due(days: int = 6) -> bool:
    raw = str(get_settings().get("citation_refreshed_at") or "")
    if not raw:
        return True
    try:
        last = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return True
    return (datetime.now() - last).days >= days


def mark_citation_refreshed() -> None:
    save_settings({"citation_refreshed_at": now_iso()})


# ---------- 本地 PDF 库 ----------

def local_pdf_path(paper_id: int) -> Any:
    return config.PDF_DIR / f"{paper_id}.pdf"


def get_local_pdf(paper_id: int) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM local_pdfs WHERE paper_id=?", (paper_id,)).fetchone()
    return dict(row) if row else None


def save_local_pdf(paper_id: int, size: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO local_pdfs(paper_id, size, saved_at) VALUES(?,?,?) "
            "ON CONFLICT(paper_id) DO UPDATE SET size=excluded.size, saved_at=excluded.saved_at",
            (paper_id, int(size), now_iso()),
        )


def remove_local_pdf(paper_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM local_pdfs WHERE paper_id=?", (paper_id,))


def local_pdf_stats() -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(size),0) AS b FROM local_pdfs").fetchone()
    return {"count": int(row["c"]), "bytes": int(row["b"])}


def prune_missing_local_pdfs() -> int:
    """清理数据库中有记录但文件已被删除的条目，返回清理数量。"""
    removed = 0
    with db() as conn:
        rows = conn.execute("SELECT paper_id FROM local_pdfs").fetchall()
        for row in rows:
            if not local_pdf_path(int(row["paper_id"])).exists():
                conn.execute("DELETE FROM local_pdfs WHERE paper_id=?", (row["paper_id"],))
                removed += 1
    return removed


# ---------- 相关论文推荐 ----------

def related_papers(paper_id: int, limit: int = 6) -> list[dict[str, Any]]:
    """基于同作者、同主题、同关键词与同学期刊的本地推荐，返回带推荐理由的列表。"""
    paper = get_paper(paper_id)
    if not paper:
        return []
    authors = set(paper.get("authors") or []) | set(paper.get("authors_en") or [])
    topics = set(paper.get("topics") or [])
    keywords = set(paper.get("keywords") or []) | set(paper.get("keywords_en") or [])
    journal_id = paper.get("journal_id")

    candidates: dict[int, dict[str, Any]] = {}
    with db() as conn:
        recent = conn.execute(
            """
            SELECT p.id FROM papers p
            WHERE p.archived=0 AND p.id != ? AND p.journal_id = ?
            ORDER BY p.first_seen_at DESC LIMIT 40
            """,
            (paper_id, journal_id),
        ).fetchall()
        for row in recent:
            candidates[int(row["id"])] = {"journal": 2}
        if topics:
            marks = ",".join("?" for _ in topics)
            rows = conn.execute(
                f"""
                SELECT DISTINCT p.id FROM papers p, json_each(p.topics) t
                WHERE p.archived=0 AND p.id != ? AND t.value IN ({marks})
                ORDER BY p.first_seen_at DESC LIMIT 60
                """,
                (paper_id, *sorted(topics)),
            ).fetchall()
            for row in rows:
                candidates.setdefault(int(row["id"]), {})["topics"] = 2
        if keywords:
            marks = ",".join("?" for _ in keywords)
            rows = conn.execute(
                f"""
                SELECT DISTINCT p.id FROM papers p
                WHERE p.archived=0 AND p.id != ?
                  AND (EXISTS (SELECT 1 FROM json_each(p.keywords) k WHERE k.value IN ({marks}))
                    OR EXISTS (SELECT 1 FROM json_each(p.keywords_en) k WHERE k.value IN ({marks})))
                ORDER BY p.first_seen_at DESC LIMIT 60
                """,
                (paper_id, *sorted(keywords), *sorted(keywords)),
            ).fetchall()
            for row in rows:
                candidates.setdefault(int(row["id"]), {})["keywords"] = 1

    if not candidates:
        return []
    ids = sorted(candidates)
    scored: list[tuple[int, int, list[str]]] = []
    authors_lower = {a.lower() for a in authors if a}
    for pid in ids:
        other = get_paper(pid)
        if not other:
            continue
        score = 0
        reasons: list[str] = []
        other_authors = set(other.get("authors") or []) | set(other.get("authors_en") or [])
        shared_authors = {a for a in other_authors if a.lower() in authors_lower}
        if shared_authors:
            score += 4
            reasons.append("同作者：" + ", ".join(sorted(shared_authors)[:2]))
        shared_topics = topics & set(other.get("topics") or [])
        if shared_topics:
            score += 2 + min(len(shared_topics), 3)
            reasons.append("同主题")
        shared_kw = keywords & (set(other.get("keywords") or []) | set(other.get("keywords_en") or []))
        if shared_kw:
            score += 1 + min(len(shared_kw), 2)
            reasons.append("关键词相近")
        if other.get("journal_id") == journal_id:
            score += 1
            reasons.append("同期刊")
        if score > 0:
            scored.append((score, pid, reasons))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    items = []
    for score, pid, reasons in scored[:limit]:
        item = get_paper(pid)
        if item:
            item["related_score"] = score
            item["related_reasons"] = reasons
            items.append(item)
    return items
