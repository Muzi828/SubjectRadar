from __future__ import annotations

import gzip
import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from . import config

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "section"):
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_title(value: str) -> str:
    value = strip_html(value)
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_title(value: str) -> str:
    title = strip_html(value)
    title = re.sub(r"\s+", " ", title).strip(" .")
    return title


def extract_doi(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    match = re.search(r"10\.\d{4,9}/[^\s\"<>]+", value, re.I)
    if not match:
        return ""
    doi = match.group(0).rstrip(".,;)&")
    return doi.lower()


def parse_authors(value: str | None) -> list[str]:
    if not value:
        return []
    text = strip_html(value)
    text = re.sub(r"^\s*(author\(s\)|authors?)\s*:\s*", "", text, flags=re.I)
    text = text.replace("，", ", ").replace("、", ", ")
    if ";" in text:
        parts = re.split(r"\s*;\s*", text)
    elif re.search(r",\s*(?:[A-Z]\.\s*){1,3}[A-Z]?\.?", text):
        parts = re.split(r",\s*(?=[^,]+,\s*(?:[A-Z]\.\s*){1,3}[A-Z]?\.?)", text)
        if len(parts) == 1:
            parts = re.split(r"\s*,\s*", text)
    else:
        parts = re.split(r"\s*,\s*", text)
    authors = []
    for part in parts:
        name = re.sub(r"\s+", " ", part).strip(" ,;")
        if name and len(name) > 1 and name.lower() not in {"author", "authors"}:
            authors.append(name)
    return authors[:80]


def parse_date_text(value: str | None) -> tuple[str | None, str]:
    if not value:
        return None, "day"
    text = strip_html(value).strip()
    if not text:
        return None, "day"
    m = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "day"
    try:
        dt = parsedate_to_datetime(text)
        return dt.strftime("%Y-%m-%d"), "day"
    except (TypeError, ValueError, OverflowError):
        pass
    m = re.search(r"\b([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b", text)
    if m and m.group(1).lower() in MONTHS:
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}", "day"
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})\b", text)
    if m and m.group(2).lower() in MONTHS:
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}", "day"
    m = re.search(r"\b([A-Za-z]+)\.?\s+(\d{4})\b", text)
    if m and m.group(1).lower() in MONTHS:
        return f"{int(m.group(2)):04d}-{MONTHS[m.group(1).lower()]:02d}-01", "month"
    m = re.search(r"\b(19|20)\d{2}\b", text)
    if m:
        return f"{m.group(0)}-01-01", "year"
    return None, "day"


def should_skip_title(title: str) -> bool:
    raw = strip_html(title)
    if any(marker in raw for marker in ("评《", "书评", "——评", "简讯", "会议报道", "征稿启事", "编者按")):
        return True
    t = normalize_title(title)
    if not t:
        return True
    exact = {
        "editorial board", "editorial board members", "call for papers", "preface",
        "foreword", "contents", "table of contents", "issue information",
        "in memoriam", "obituary", "publisher note", "publishers note",
    }
    if t in exact:
        return True
    starts = (
        "editorial board", "corrigendum", "erratum", "retraction", "retracted",
        "expression of concern", "publisher s note", "publishers note",
        "call for papers", "preface to", "foreword to", "in memoriam", "obituary",
        "introduction to the special issue", "guest editorial", "editorial ",
    )
    return t.startswith(starts)


def build_topics(title: str, abstract: str = "", keywords: list[str] | None = None) -> list[str]:
    haystack = " ".join([title or "", abstract or "", " ".join(keywords or [])]).lower()
    topics = []
    for name, pattern in config.TOPIC_RULES:
        if re.search(pattern, haystack, re.I):
            topics.append(name)
        if len(topics) >= 3:
            break
    if not topics:
        topics.append("安全科学综合")
    return topics


def make_fingerprint(doi: str, pii: str, title: str, journal_id: int) -> str:
    if doi:
        return "doi:" + doi.lower()
    if pii:
        return "pii:" + pii.upper()
    basis = f"{normalize_title(title)}|{journal_id}"
    return "title:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()


def http_get(url: str, *, timeout: int = 20, retries: int = 1, accept: str = "*/*") -> bytes:
    headers = {
        "User-Agent": config.BROWSER_UA if any(k in url for k in ("sciencedirect", "springer", "wiley", "bmj", "cssjj", "paperopen", "jc2.org.cn")) else config.USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, identity",
        "Connection": "close",
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip" or data[:2] == b"\x1f\x8b":
                    try:
                        data = gzip.decompress(data)
                    except OSError:
                        pass
                return data
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
    raise last_error or RuntimeError("网络请求失败")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, names: tuple[str, ...]) -> str:
    names = tuple(n.lower() for n in names)
    for child in list(element):
        if _local(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _longest_text(element: ET.Element, names: tuple[str, ...]) -> str:
    names = tuple(n.lower() for n in names)
    values = []
    for child in list(element):
        if _local(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                values.append(text)
    return max(values, key=len) if values else ""


def _child_text_by_local(element: ET.Element, local_name: str) -> str:
    for child in list(element):
        if _local(child.tag) == local_name.lower():
            return "".join(child.itertext()).strip()
    return ""


def _find_item_elements(root: ET.Element) -> list[ET.Element]:
    return [el for el in root.iter() if _local(el.tag) == "item"]


def _rss_items(xml_bytes: bytes, journal: dict[str, Any]) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    items = _find_item_elements(root)
    result: list[dict[str, Any]] = []
    feed_type = journal.get("feed_type") or "generic"
    for element in items:
        title = clean_title(_child_text(element, ("title",)))
        if should_skip_title(title):
            continue
        link = _child_text(element, ("link",))
        guid = _child_text(element, ("guid",))
        doi = extract_doi(_child_text(element, ("doi", "identifier", "guid")))
        description = _child_text(element, ("encoded", "description"))
        abstract = ""
        authors: list[str] = []
        published_date = None
        published_precision = "day"
        online_date = None
        volume = ""
        issue = ""
        pages = ""
        section = ""
        pii = ""
        doi_url = ""

        if feed_type == "elsevier":
            raw_desc = _child_text(element, ("description",))
            date_match = re.search(r"Publication date:\s*([^<]+)", raw_desc, re.I)
            src_match = re.search(r"Source:\s*([^<]+)", raw_desc, re.I)
            author_match = re.search(r"Author\(s\):\s*([^<]*)", raw_desc, re.I)
            if date_match:
                published_date, published_precision = parse_date_text(date_match.group(1))
            if src_match:
                source_text = strip_html(src_match.group(1))
                vol = re.search(r"Volume\s+([\w\-]+)", source_text, re.I)
                if vol:
                    volume = vol.group(1)
                iss = re.search(r"Issue\s+([\w\-]+)", source_text, re.I)
                if iss:
                    issue = iss.group(1)
                section = source_text
            if author_match:
                authors = parse_authors(author_match.group(1))
            m = re.search(r"/pii/([A-Z0-9]+)", link, re.I)
            if m:
                pii = m.group(1).upper()
            online_m = re.search(r"(?:Available online|online)\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})", raw_desc, re.I)
            if online_m:
                online_date, _ = parse_date_text(online_m.group(1))
            # Elsevier 的 RSS 只提供题录信息，不提供摘要；摘要留给 Crossref/OpenAlex 补充。
            abstract = ""
            source_label = "ScienceDirect RSS"
        elif feed_type == "springer":
            abstract = strip_html(description)
            pub = _child_text(element, ("pubDate", "date"))
            published_date, published_precision = parse_date_text(pub)
            online_date = published_date
            doi = extract_doi(guid) or extract_doi(link)
            source_label = "Springer Link RSS"
            if doi and not link:
                link = f"https://doi.org/{doi}"
        elif feed_type == "wiley":
            abstract = strip_html(_longest_text(element, ("encoded", "description")))
            abstract = re.sub(r"^\s*ABSTRACT\s*", "", abstract, flags=re.I)
            authors = parse_authors(_child_text(element, ("creator",)))
            cover = _child_text_by_local(element, "coverdate") or _child_text(element, ("date", "pubDate"))
            published_date, published_precision = parse_date_text(cover)
            online_date, _ = parse_date_text(_child_text(element, ("date", "pubDate")))
            volume = _child_text(element, ("volume",))
            issue = _child_text(element, ("number", "issue"))
            section = _child_text(element, ("section", "category"))
            doi = doi or extract_doi(_child_text(element, ("doi", "identifier", "guid")))
            source_label = "Wiley Online Library RSS"
        elif feed_type == "bmj":
            abstract = strip_html(description)[:2400]
            authors = parse_authors(_child_text(element, ("creator",)))
            pub_text = _child_text_by_local(element, "publicationdate") or _child_text(element, ("date", "pubDate"))
            published_date, published_precision = parse_date_text(pub_text)
            online_date, _ = parse_date_text(_child_text(element, ("date",)))
            volume = _child_text(element, ("volume",))
            issue = _child_text(element, ("number", "issue"))
            start = _child_text(element, ("startingPage",))
            end = _child_text(element, ("endingPage",))
            pages = f"{start}-{end}" if start and end else start
            section = _child_text(element, ("section",))
            doi = doi or extract_doi(" ".join(_child_text(element, (n,)) for n in ("identifier",)))
            source_label = "BMJ RSS"
        else:
            abstract = strip_html(description)
            authors = parse_authors(_child_text(element, ("creator", "author")))
            published_date, published_precision = parse_date_text(_child_text(element, ("date", "pubDate")))
            online_date = published_date
            doi = doi or extract_doi(link)
            source_label = "期刊官网 RSS"

        if not title:
            continue
        if not link and doi:
            link = f"https://doi.org/{doi}"
        if doi:
            doi_url = f"https://doi.org/{doi}"
        if not link:
            link = doi_url
        result.append({
            "title": title,
            "authors": authors,
            "doi": doi,
            "pii": pii,
            "link": link,
            "doi_url": doi_url,
            "abstract": abstract[:6000],
            "published_date": published_date,
            "published_precision": published_precision,
            "online_date": online_date,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "section": section,
            "source": "rss",
            "source_label": source_label,
        })
    return result


def parse_feed(xml_bytes: bytes, journal: dict[str, Any]) -> list[dict[str, Any]]:
    items = _rss_items(xml_bytes, journal)
    # 有些 feed 会重复推送同一篇文章，先按标题+DOI 去重。
    seen = set()
    unique = []
    for item in items:
        key = (item.get("doi") or "").lower() or normalize_title(item["title"])
        if key in seen:
            continue
        seen.add(key)
        item["fingerprint"] = make_fingerprint(item.get("doi", ""), item.get("pii", ""), item["title"], journal["id"])
        item["topics"] = build_topics(item["title"], item.get("abstract", ""))
        unique.append(item)
    return unique


def fetch_crossref_works(issn: str, *, days: int = 180, rows: int = 200) -> list[dict[str, Any]]:
    if not issn:
        return []
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "filter": f"from-created-date:{since},type:journal-article",
        "rows": str(rows),
        "sort": "created",
        "order": "desc",
        "mailto": "safelens.local@example.com",
    }
    url = f"https://api.crossref.org/journals/{urllib.parse.quote(issn)}/works?{urllib.parse.urlencode(params)}"
    try:
        raw = http_get(url, accept="application/json", timeout=20, retries=1)
        payload = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        params.pop("filter", None)
        params["filter"] = f"from-created-date:{since}"
        url = f"https://api.crossref.org/journals/{urllib.parse.quote(issn)}/works?{urllib.parse.urlencode(params)}"
        raw = http_get(url, accept="application/json", timeout=20, retries=1)
        payload = json.loads(raw.decode("utf-8", "replace"))
    messages = payload.get("message", {}).get("items", [])
    result = []
    for msg in messages:
        title = clean_title((msg.get("title") or [""])[0])
        if should_skip_title(title):
            continue
        doi = (msg.get("DOI") or "").lower()
        authors = []
        for author in msg.get("author") or []:
            name = " ".join(x for x in [author.get("given"), author.get("family")] if x).strip()
            if not name:
                name = author.get("name") or ""
            if name:
                authors.append(name)
        abstract = strip_html(msg.get("abstract") or "")
        abstract = re.sub(r"^\s*Abstract\s*", "", abstract, flags=re.I)
        dates = msg.get("published-print") or msg.get("published-online") or msg.get("issued") or {}
        date_parts = (dates or {}).get("date-parts") or [[]]
        parts = date_parts[0] if date_parts else []
        published_date = None
        precision = "day"
        if parts:
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else 1
            day = int(parts[2]) if len(parts) > 2 else 1
            published_date = f"{year:04d}-{month:02d}-{day:02d}"
            precision = "day" if len(parts) > 2 else ("month" if len(parts) > 1 else "year")
        created = (msg.get("created") or {}).get("date-time", "")
        online_date = created[:10] if created else None
        online_parts = (msg.get("published-online") or {}).get("date-parts") or [[]]
        if online_parts and online_parts[0]:
            p = online_parts[0]
            oy = int(p[0])
            om = int(p[1]) if len(p) > 1 else 1
            od = int(p[2]) if len(p) > 2 else 1
            online_date = f"{oy:04d}-{om:02d}-{od:02d}"
        result.append({
            "title": title,
            "authors": authors,
            "doi": doi,
            "doi_url": f"https://doi.org/{doi}" if doi else "",
            "link": f"https://doi.org/{doi}" if doi else (msg.get("URL") or ""),
            "abstract": abstract[:6000],
            "published_date": published_date,
            "published_precision": precision,
            "online_date": online_date,
            "volume": msg.get("volume") or "",
            "issue": msg.get("issue") or "",
            "pages": msg.get("page") or "",
            "section": "",
            "source": "crossref",
            "source_label": "Crossref 元数据",
            "citation_count": int(msg.get("is-referenced-by-count") or 0),
            "created_date": created[:10] if created else "",
        })
    for item in result:
        item["fingerprint"] = make_fingerprint(item.get("doi", ""), "", item["title"], 0)
        item["topics"] = build_topics(item["title"], item.get("abstract", ""))
    return result


def _meta_values(html_text: str, name: str) -> list[str]:
    values = []
    name_re = re.compile(r"\bname\s*=\s*['\"]" + re.escape(name) + r"['\"]", re.I)
    content_re = re.compile(r"\bcontent\s*=\s*(['\"])(.*?)\1", re.I | re.S)
    for tag_match in re.finditer(r"<meta\b[^>]*>", html_text, re.I):
        tag = tag_match.group(0)
        if not name_re.search(tag):
            continue
        content_match = content_re.search(tag)
        if content_match:
            value = html.unescape(content_match.group(2)).strip()
            if value:
                values.append(value)
    return values


def _first_meta(html_text: str, name: str) -> str:
    values = _meta_values(html_text, name)
    return values[0] if values else ""


def _cn_date(value: str) -> tuple[str | None, str]:
    return parse_date_text(value)


def parse_cssjj_detail(html_text: str) -> dict[str, Any]:
    title = clean_title(_first_meta(html_text, "citation_title"))
    authors = parse_authors(_first_meta(html_text, "citation_authors"))
    doi = extract_doi(_first_meta(html_text, "citation_doi"))
    volume = _first_meta(html_text, "citation_volume")
    issue = _first_meta(html_text, "citation_issue")
    first_page = _first_meta(html_text, "citation_firstpage")
    last_page = _first_meta(html_text, "citation_lastpage")
    pages = f"{first_page}-{last_page}" if first_page and last_page else first_page
    published_date, precision = _cn_date(_first_meta(html_text, "citation_publication_date"))
    online_date, _ = _cn_date(_first_meta(html_text, "citation_online_date"))
    if online_date:
        try:
            if (datetime.strptime(online_date, "%Y-%m-%d") - datetime.now()).days > 120:
                online_date = None
        except ValueError:
            online_date = None
    keyword_values = _meta_values(html_text, "DC.Keywords")
    if not keyword_values:
        keyword_values = _meta_values(html_text, "citation_keywords")
    keywords: list[str] = []
    for value in keyword_values:
        for part in re.split(r"[,，;；]", value):
            part = part.strip(" .;；,，")
            if part and part not in keywords:
                keywords.append(part)
    keywords = keywords[:12]
    abstract = ""
    match = re.search(r"<strong>\s*摘要[：:]\s*</strong>(.*?)<form", html_text, re.I | re.S)
    if match:
        abstract = strip_html(match.group(1))
    if not abstract:
        abstract = strip_html(_first_meta(html_text, "Description"))
    return {
        "title": title,
        "authors": authors,
        "doi": doi,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "published_date": published_date,
        "published_precision": precision,
        "online_date": online_date,
        "abstract": abstract[:6000],
        "keywords": keywords,
        "section": "",
    }


def fetch_jc2_current(journal: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    raw = http_get(journal["rss_url"], accept="application/rss+xml, application/xml, text/xml, */*")
    root = ET.fromstring(raw)
    seen: dict[str, dict[str, Any]] = {}
    for element in root.iter():
        if _local(element.tag) != "item":
            continue
        title = clean_title(_child_text(element, ("title",)))
        if not title or should_skip_title(title):
            continue
        link = _child_text(element, ("link", "url"))
        doi = extract_doi(_child_text(element, ("doi", "identifier", "guid")))
        if not doi and not link:
            continue
        description = _longest_text(element, ("encoded", "description"))
        abstract = clean_jc2_abstract(description)[:5000]
        volume = _child_text(element, ("volume",))
        issue = _child_text(element, ("number", "issue"))
        start = _child_text(element, ("startingPage",))
        end = _child_text(element, ("endingPage",))
        pages = f"{start}-{end}" if start and end and end not in start else start
        key = doi or link or title
        if key in seen:
            continue
        seen[key] = {
            "title": title,
            "authors": [],
            "doi": doi,
            "doi_url": f"https://doi.org/{doi}" if doi else "",
            "link": link,
            "url": link,
            "preview_url": (link + ("?viewType=HTML" if "?" not in link else "&viewType=HTML")) if link else "",
            "abstract": abstract,
            "keywords": [],
            "published_date": None,
            "published_precision": "day",
            "online_date": None,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "section": "",
            "source": "official",
            "source_label": "《指挥与控制学报》官网",
            "needs_detail": True,
        }
    items = list(seen.values())[:limit]
    toc_authors, issue_date = _jc2_toc(journal)
    for item in items:
        if item.get("doi") and toc_authors.get(item["doi"].lower()):
            item["authors"] = toc_authors[item["doi"].lower()]
        if issue_date:
            item["published_date"] = issue_date
            item["published_precision"] = "day"
    # Fall back to one article page only if the TOC did not expose the issue date.
    if items and not issue_date and items[0].get("url"):
        try:
            page = http_get(items[0]["url"].replace("https://", "http://"), accept="text/html,application/xhtml+xml", timeout=25, retries=1).decode("utf-8", "replace")
            published, precision = parse_date_text(_jc2_meta(page, "citation_date"))
            online, _ = parse_date_text(_jc2_meta(page, "citation_online_date"))
            authors = parse_authors(_jc2_meta(page, "citation_authors") or _jc2_meta(page, "citation_author"))
            if published:
                for item in items:
                    item["published_date"] = published
                    item["published_precision"] = precision or "day"
                    item["online_date"] = online
                if authors:
                    items[0]["authors"] = authors
        except Exception:
            pass
    return items


def clean_jc2_abstract(text: str) -> str:
    value = text or ""
    for _ in range(3):
        value = html.unescape(value)
    value = re.sub(r"<\s*br\s*/?\s*>", " ", value, flags=re.I)
    value = re.sub(r"<\s*/?\s*p\s*<\s*", " ", value, flags=re.I)
    value = re.sub(r"<\s*/?\s*p\s*>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"指挥与控制学报\.\s*\d{4}.*$", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _jc2_toc(journal: dict[str, Any]) -> tuple[dict[str, list[str]], str | None]:
    toc_url = journal.get("homepage_url", "").rstrip("/").replace("https://", "http://") + "/cn/article/current"
    try:
        page = http_get(toc_url, accept="text/html,application/xhtml+xml", timeout=25, retries=1).decode("utf-8", "replace")
    except Exception:
        return {}, None
    result: dict[str, list[str]] = {}
    published = None
    stamp = re.search(r'"publishDate"\s*:\s*(\d{10,13})', page)
    if stamp:
        seconds = int(stamp.group(1)) / (1000 if len(stamp.group(1)) == 13 else 1)
        try:
            published = datetime.fromtimestamp(seconds).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            pass
    pattern = re.compile(
        r"<div class=\"article-list-title[^>]*>.*?<a href=['\"]/article/doi/([^'\"]+)[^'\"]*['\"][^>]*>.*?</a>.*?"
        r"<div class=\"article-list-author[^>]*>(.*?)</div>",
        re.S | re.I,
    )
    for match in pattern.finditer(page):
        doi = match.group(1).strip().lower()
        names = re.findall(r"authorNameCn['\"]\s*,\s*['\"]([^'\"]+)", match.group(2))
        if doi and names:
            result[doi] = [html.unescape(name).strip() for name in names if name.strip()]
    return result, published


def _jc2_meta(text: str, name: str) -> str:
    pattern = re.compile(
        r'<meta[^>]+(?:name|property)\s*=\s*["\']' + re.escape(name) + r'["\'][^>]*content\s*=\s*["\']([^"\']*)',
        re.I,
    )
    match = pattern.search(text)
    if match:
        return html.unescape(match.group(1)).strip()
    pattern = re.compile(
        r'<meta[^>]+content\s*=\s*["\']([^"\']*)["\'][^>]+(?:name|property)\s*=\s*["\']' + re.escape(name) + r'["\']',
        re.I,
    )
    match = pattern.search(text)
    return html.unescape(match.group(1)).strip() if match else ""


def enrich_jc2_details(items: list[dict[str, Any]], max_workers: int = 1) -> int:
    targets = [item for item in items if item.get("needs_detail") and item.get("url")]
    if not targets:
        return 0

    for item in targets:
        url = item["url"].replace("https://", "http://")
        en_url = url.replace("/article/doi/", "/en/article/doi/")
        detail: dict[str, Any] = {}
        en_page = ""
        try:
            en_page = http_get(en_url, accept="text/html,application/xhtml+xml", timeout=30, retries=2).decode("utf-8", "replace")
            title_en = _jc2_meta(en_page, "citation_title")
            authors_en = parse_authors(_jc2_meta(en_page, "citation_authors") or _jc2_meta(en_page, "citation_author"))
            keywords_en = [x.strip() for x in re.split(r"[,，;；]", _jc2_meta(en_page, "citation_keywords")) if x.strip()]
            abstract_en = clean_jc2_abstract(_jc2_meta(en_page, "dc.description"))
            online, _ = parse_date_text(_jc2_meta(en_page, "citation_online_date"))
            published, precision = parse_date_text(_jc2_meta(en_page, "citation_date"))
            pdf = _jc2_meta(en_page, "citation_pdf_url")
            preview = _jc2_meta(en_page, "citation_abstract_html_url")
            if title_en:
                detail["title_en"] = title_en
            if authors_en:
                detail["authors_en"] = authors_en
            if keywords_en:
                detail["keywords_en"] = keywords_en
            if abstract_en:
                detail["abstract_en"] = abstract_en
            if online:
                detail["online_date"] = online
            if published:
                detail["published_date"] = published
                detail["published_precision"] = precision or "day"
            if pdf:
                detail["pdf_url"] = pdf
            if preview:
                detail["preview_url"] = preview
            if title_en:
                names = ", ".join(authors_en) if authors_en else "Unknown"
                year = (published or item.get("published_date") or "")[:4]
                cit = f"{names}. {title_en}[J]. Journal of Command and Control, {year}"
                if item.get("volume"):
                    cit += f", {item['volume']}"
                if item.get("issue"):
                    cit += f"({item['issue']})"
                if item.get("pages"):
                    cit += f": {item['pages']}"
                cit += "."
                if item.get("doi"):
                    cit += f" DOI: {item['doi']}."
                detail["citation_text_en"] = cit
        except Exception:
            pass
        try:
            cn_page = http_get(url, accept="text/html,application/xhtml+xml", timeout=30, retries=2).decode("utf-8", "replace")
            cn_keywords = [x.strip() for x in re.split(r"[,，;；]", _jc2_meta(cn_page, "citation_keywords")) if x.strip()]
            if cn_keywords:
                detail["keywords"] = cn_keywords
            for key, meta_name in (
                ("affiliations", "citation_author_institution"),
                ("funding", "citation_funding"),
                ("received_date", "citation_received_date"),
                ("revised_date", "citation_revised_date"),
                ("accepted_date", "citation_accepted_date"),
            ):
                value = _jc2_meta(cn_page, meta_name) or _jc2_meta(en_page, meta_name)
                if value:
                    detail[key] = value
        except Exception:
            pass
        for key, value in detail.items():
            if value:
                item[key] = value
        item["needs_detail"] = False
        time.sleep(0.6)
    return len(targets)


def fetch_cssjj_toc(journal: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    raw = http_get(journal["rss_url"], accept="text/html,application/xhtml+xml")
    text = raw.decode("utf-8", "replace")
    pattern = re.compile(r'<a[^>]+href="([^"]*?/CN/(10\.\d{4,9}/[^"#?]+))"[^>]*>(.*?)</a>', re.S | re.I)
    seen: dict[str, dict[str, Any]] = {}
    for match in pattern.finditer(text):
        href = match.group(1)
        doi = match.group(2).strip().lower()
        title = clean_title(match.group(3))
        if not title or title.startswith("10.") or doi in seen:
            continue
        url = urllib.parse.urljoin("http://www.cssjj.com.cn/", href)
        published_date = None
        precision = "month"
        year_month = re.search(r"\.(\d{4})\.(\d{2})\.", doi)
        if year_month:
            published_date = f"{year_month.group(1)}-{year_month.group(2)}-01"
        seen[doi] = {
            "title": title,
            "authors": [],
            "doi": doi,
            "doi_url": f"https://doi.org/{doi}",
            "link": url,
            "url": url,
            "abstract": "",
            "keywords": [],
            "published_date": published_date,
            "published_precision": precision,
            "online_date": None,
            "volume": "",
            "issue": "",
            "pages": "",
            "section": "",
            "source": "official",
            "source_label": "《中国安全科学学报》官网",
            "needs_detail": True,
        }
    return list(seen.values())[:limit]


def enrich_cssjj_details(items: list[dict[str, Any]], max_workers: int = 6) -> int:
    targets = [item for item in items if item.get("needs_detail") and item.get("url")]
    if not targets:
        return 0

    def work(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        try:
            raw = http_get(item["url"], accept="text/html,application/xhtml+xml", timeout=25, retries=1)
            return item, parse_cssjj_detail(raw.decode("utf-8", "replace"))
        except Exception:
            return item, None

    enriched = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(work, item) for item in targets]
        for future in as_completed(futures):
            item, detail = future.result()
            item["needs_detail"] = False
            if not detail:
                continue
            for key in ("title", "authors", "doi", "volume", "issue", "pages",
                        "published_date", "published_precision", "online_date",
                        "abstract", "keywords", "section"):
                value = detail.get(key)
                if value:
                    item[key] = value
            if item.get("doi"):
                item["doi_url"] = f"https://doi.org/{item['doi']}"
            enriched += 1
    return enriched


def fetch_aqsckj(journal: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    raw = http_get(journal["rss_url"], accept="application/json", timeout=30, retries=2)
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        payload = json.loads(raw.decode("gb18030", "replace"))
    rows = payload.get("Data") or []
    result: list[dict[str, Any]] = []
    for row in rows:
        title = clean_title(str(row.get("Title") or ""))
        if not title or should_skip_title(title):
            continue
        authors = parse_authors(str(row.get("AuthorsList") or ""))
        abstract = strip_html(str(row.get("Abstract_Chinese") or ""))[:6000]
        published_date, precision = parse_date_text(str(row.get("DateCreate") or row.get("Publish_Date") or ""))
        doi = extract_doi(str(row.get("DOI") or ""))
        article_num = str(row.get("Article_Num") or "").strip()
        article_id = row.get("ArticleID")
        url = f"http://aqsckj.paperopen.com/#/digest?ArticleID={article_id}" if article_id else "http://aqsckj.paperopen.com/"
        first_page, last_page = str(row.get("Page_Num") or ""), str(row.get("Page_NumEnd") or "")
        pages = f"{first_page}-{last_page}" if first_page and last_page else first_page
        result.append({
            "title": title,
            "authors": authors,
            "doi": doi,
            "doi_url": f"https://doi.org/{doi}" if doi else "",
            "pii": article_num or None,
            "link": url,
            "url": url,
            "abstract": abstract,
            "keywords": [],
            "published_date": published_date,
            "published_precision": precision,
            "online_date": published_date,
            "volume": str(row.get("Volume") or ""),
            "issue": str(row.get("IssueNum") or ""),
            "pages": pages,
            "section": str(row.get("Column") or ""),
            "source": "official",
            "source_label": "《中国安全生产科学技术》官网",
            "open_access": bool(row.get("IsOA")),
            "citation_count": 0,
            "needs_detail": False,
        })
    return result[:limit]


def parse_cast_detail(html_text: str) -> dict[str, Any]:
    doi_match = re.search(r"DOI\s*[:：]\s*(10\.\d{4,9}/[^\s<]+)", html_text, re.I)
    doi = extract_doi(doi_match.group(1)) if doi_match else ""
    keywords: list[str] = []
    kw_match = re.search(r"关键词</div>.*?<div class=\"neirong\">(.*?)</div>", html_text, re.I | re.S)
    if kw_match:
        kw_text = strip_html(kw_match.group(1))
        for part in re.split(r"\s*/\s*", kw_text):
            part = part.strip(" ;；,，")
            if part and part not in keywords:
                keywords.append(part)
    pub_match = re.search(r"(?:出版时间|发布时间)\s*[:：]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", html_text, re.I)
    published_date = pub_match.group(1) if pub_match else None
    return {"doi": doi, "keywords": keywords[:12], "published_date": published_date}


def fetch_cast_current(journal: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
    raw = http_get(journal["rss_url"], accept="text/html,application/xhtml+xml", timeout=30, retries=1)
    text = raw.decode("utf-8", "replace")
    blocks = re.findall(r'<li([^>]*latest_article_item_li[^>]*)>(.*?)</li>', text, re.I | re.S)
    result: list[dict[str, Any]] = []
    for attrs, block in blocks:
        title_match = re.search(r'j-title-1[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.I | re.S)
        if not title_match:
            continue
        url = urllib.parse.urljoin("https://castjournals.cast.org.cn", title_match.group(1))
        title = clean_title(title_match.group(2))
        if not title or should_skip_title(title):
            continue
        author_match = re.search(r'j-author[^>]*>(.*?)</div>', block, re.I | re.S)
        authors = parse_authors(author_match.group(1) if author_match else "")
        abstract_match = re.search(r'j-abstract[^>]*>(.*?)</div>', block, re.I | re.S)
        abstract = strip_html(abstract_match.group(1)) if abstract_match else ""
        online_match = re.search(r"var onlineDate\s*=\s*'([^']+)'", block)
        online_date, precision = parse_date_text(online_match.group(1) if online_match else "")
        vol_match = re.search(r'volume="([^"]*)"', attrs)
        iss_match = re.search(r'issue="([^"]*)"', attrs)
        year_match = re.search(r'year="([^"]*)"', attrs)
        article_id = url.rstrip("/").rsplit("/", 1)[-1]
        result.append({
            "title": title,
            "authors": authors,
            "doi": "",
            "doi_url": "",
            "pii": article_id,
            "link": url,
            "url": url,
            "abstract": abstract[:6000],
            "keywords": [],
            "published_date": online_date,
            "published_precision": precision,
            "online_date": online_date,
            "volume": vol_match.group(1) if vol_match else "",
            "issue": iss_match.group(1) if iss_match else "",
            "pages": "",
            "section": "",
            "source": "official",
            "source_label": "《安全与环境学报》官网",
            "open_access": False,
            "citation_count": 0,
            "needs_detail": True,
        })
    return result[:limit]


def fetch_cnki_current(journal: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
    raw = http_get(journal["rss_url"], accept="text/html,application/xhtml+xml", timeout=30, retries=1)
    text = raw.decode("utf-8", "replace")
    chunks = text.split('<div class="paperMain">')[1:]
    result: list[dict[str, Any]] = []
    for chunk in chunks:
        title_match = re.search(r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.I | re.S)
        if not title_match:
            continue
        url = title_match.group(1).strip()
        title = clean_title(title_match.group(2))
        if not title or should_skip_title(title):
            continue
        after_title = chunk[title_match.end():]
        author_match = re.search(r"<span>(.*?)</span>", after_title, re.I | re.S)
        authors = parse_authors(author_match.group(1) if author_match else "")
        abstract_match = re.search(r"<p>(.*?)</p>", after_title, re.I | re.S)
        abstract = strip_html(abstract_match.group(1)) if abstract_match else ""
        info_match = re.search(r'publish-info[^>]*>(.*?)</span>', chunk, re.I | re.S)
        info_text = strip_html(info_match.group(1)) if info_match else ""
        year_match = re.search(r"(\d{4})\s*年", info_text)
        issue_match = re.search(r"(\d{1,2})\s*期", info_text)
        volume_match = re.search(r"v\.\s*(\d+)", info_text, re.I)
        year = int(year_match.group(1)) if year_match else 0
        issue = int(issue_match.group(1)) if issue_match else 0
        published_date = f"{year:04d}-{issue:02d}-01" if year and issue else None
        paper_id = url.rstrip("/").rsplit("/", 1)[-1]
        result.append({
            "title": title,
            "authors": authors,
            "doi": "",
            "doi_url": "",
            "pii": paper_id,
            "link": url,
            "url": url,
            "abstract": abstract[:6000],
            "keywords": [],
            "published_date": published_date,
            "published_precision": "month",
            "online_date": None,
            "volume": volume_match.group(1) if volume_match else "",
            "issue": str(issue) if issue else "",
            "pages": "",
            "section": "",
            "source": "official",
            "source_label": "《安全与环境工程》官网",
            "open_access": False,
            "citation_count": 0,
            "needs_detail": True,
        })
    return result[:limit]


def parse_cnki_detail(html_text: str) -> dict[str, Any]:
    doi_match = re.search(r"DOI\s*[:：]\s*(10\.\d{4,9}/[^\s<]+)", html_text, re.I)
    doi = extract_doi(doi_match.group(1)) if doi_match else ""
    keywords: list[str] = []
    kw_match = re.search(r"关键词[：:]\s*</b>(.*?)</div>", html_text, re.I | re.S)
    if kw_match:
        for match in re.finditer(r"<a[^>]*>(.*?)</a>", kw_match.group(1), re.I | re.S):
            part = strip_html(match.group(1)).strip(" ;；,，")
            if part and part not in keywords:
                keywords.append(part)
    pub_match = re.search(r"(?:发布时间|出版时间)\s*[:：]\s*(?:</b>)?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", html_text, re.I)
    pages = ""
    page_match = re.search(r"安全与环境工程\s*,\s*\d{4}\s*,\s*\d+\s*\(\s*\d+\s*\)\s*:\s*([0-9]+\s*-\s*[0-9]+)", html_text)
    if page_match:
        pages = page_match.group(1).replace(" ", "")
    return {"doi": doi, "keywords": keywords[:12], "published_date": pub_match.group(1) if pub_match else None, "pages": pages}


def _enrich_details(items: list[dict[str, Any]], parser, max_workers: int = 6) -> int:
    targets = [item for item in items if item.get("needs_detail") and item.get("url")]
    if not targets:
        return 0

    def work(item: dict[str, Any]):
        try:
            raw = http_get(item["url"], accept="text/html,application/xhtml+xml", timeout=25, retries=1)
            return item, parser(raw.decode("utf-8", "replace"))
        except Exception:
            return item, None

    enriched = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(work, item) for item in targets]
        for future in as_completed(futures):
            item, detail = future.result()
            item["needs_detail"] = False
            if not detail:
                continue
            for key in ("doi", "keywords", "published_date", "pages"):
                value = detail.get(key)
                if value:
                    item[key] = value
            if item.get("doi"):
                item["doi_url"] = f"https://doi.org/{item['doi']}"
            enriched += 1
    return enriched


def enrich_cast_details(items: list[dict[str, Any]]) -> int:
    return _enrich_details(items, parse_cast_detail)


def enrich_cnki_details(items: list[dict[str, Any]]) -> int:
    return _enrich_details(items, parse_cnki_detail)


def enrich_item_links(item: dict[str, Any], journal: dict[str, Any]) -> None:
    doi = (item.get("doi") or "").strip().lower()
    pii = (item.get("pii") or "").strip()
    url = item.get("url") or item.get("link") or item.get("doi_url") or (f"https://doi.org/{doi}" if doi else "")
    if not item.get("preview_url"):
        item["preview_url"] = url
    if not item.get("pdf_url"):
        if doi.startswith("10.1016") and pii:
            item["pdf_url"] = f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft?isDTMRedir=true&download=true"
        elif doi.startswith("10.1007"):
            item["pdf_url"] = f"https://link.springer.com/content/pdf/{doi}.pdf"
        elif doi.startswith("10.1111"):
            item["pdf_url"] = f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}"
        elif doi.startswith("10.20278"):
            item["pdf_url"] = f"http://www.jc2.org.cn/cn/article/pdf/preview/{doi}.pdf"
            item["preview_url"] = f"http://www.jc2.org.cn/cn/article/doi/{doi}?viewType=HTML"
    if not item.get("citation_text"):
        authors = item.get("authors") or []
        author_text = ", ".join(authors) if authors else "佚名"
        year = (item.get("published_date") or "")[:4] or "n.d."
        text = f"{author_text}. {item.get('title', '')}[J]. {journal.get('name', '')}, {year}"
        volume = item.get("volume") or ""
        issue = item.get("issue") or ""
        pages = item.get("pages") or ""
        if volume:
            text += f", {volume}"
        if issue:
            text += f"({issue})"
        if pages:
            text += f": {pages}"
        text += "."
        if doi:
            text += f" DOI: {doi}."
        item["citation_text"] = text


def fetch_official_items(journal: dict[str, Any], limit: int = 60) -> tuple[list[dict[str, Any]], str | None]:
    """抓取期刊官网 RSS；若失败或无 RSS，则回退 Crossref。"""
    warning = None
    items: list[dict[str, Any]] = []
    feed_type = journal.get("feed_type") or "generic"
    if feed_type == "cssjj":
        try:
            items = fetch_cssjj_toc(journal, limit=limit)
        except Exception as exc:
            warning = f"《中国安全科学学报》官网读取失败：{exc}"
    elif feed_type == "aqsckj":
        try:
            items = fetch_aqsckj(journal, limit=limit)
        except Exception as exc:
            warning = f"《中国安全生产科学技术》官网读取失败：{exc}"
    elif feed_type == "cast":
        try:
            items = fetch_cast_current(journal, limit=limit)
        except Exception as exc:
            warning = f"《安全与环境学报》官网读取失败：{exc}"
    elif feed_type == "cnki":
        try:
            items = fetch_cnki_current(journal, limit=limit)
        except Exception as exc:
            warning = f"《安全与环境工程》官网读取失败：{exc}"
    elif feed_type == "jc2":
        try:
            items = fetch_jc2_current(journal, limit=limit)
        except Exception as exc:
            warning = f"《指挥与控制学报》官网 RSS 读取失败：{exc}"
    elif journal.get("rss_url") and feed_type != "crossref":
        try:
            raw = http_get(journal["rss_url"], accept="application/rss+xml, application/xml, text/xml, */*")
            items = parse_feed(raw, journal)
        except Exception as exc:
            warning = f"官网 RSS 读取失败：{exc}"
    if not items:
        try:
            items = fetch_crossref_works(journal.get("crossref_issn") or journal.get("issn") or "", days=180, rows=max(80, limit * 3))
            if items:
                warning = (warning + "；" if warning else "") + "已回退到 Crossref 元数据"
        except Exception as exc:
            warning = (warning + "；" if warning else "") + f"Crossref 读取失败：{exc}"
    items = [it for it in items if it.get("title") and not should_skip_title(it["title"])]
    items = items[:limit]
    for item in items:
        if not item.get("fingerprint"):
            item["fingerprint"] = make_fingerprint(item.get("doi", ""), item.get("pii", ""), item["title"], journal["id"])
        if not item.get("topics"):
            item["topics"] = build_topics(item["title"], item.get("abstract", ""), item.get("keywords") or [])
    for item in items:
        enrich_item_links(item, journal)
    return items, warning


def crossref_map(issn: str, *, days: int = 180) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    works = fetch_crossref_works(issn, days=days, rows=200)
    by_title = {normalize_title(w["title"]): w for w in works if w.get("title")}
    by_doi = {w["doi"].lower(): w for w in works if w.get("doi")}
    return by_title | by_doi, works


def merge_crossref(items: list[dict[str, Any]], works: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_title = {normalize_title(w["title"]): w for w in works if w.get("title")}
    used = set()
    for item in items:
        key = normalize_title(item["title"])
        if key in by_title:
            work = by_title[key]
            used.add(work.get("doi") or work.get("fingerprint"))
            _merge_work(item, work)
        elif item.get("doi") and item["doi"].lower() in {w.get("doi", "").lower() for w in works}:
            work = next(w for w in works if w.get("doi", "").lower() == item["doi"].lower())
            used.add(work.get("doi") or work.get("fingerprint"))
            _merge_work(item, work)
    recent_cutoff = (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d")
    supplements = []
    for work in works:
        marker = work.get("doi") or work.get("fingerprint")
        if marker in used or not work.get("title"):
            continue
        if (work.get("created_date") or "") < recent_cutoff:
            continue
        if should_skip_title(work["title"]):
            continue
        supplements.append(work)
        if len(supplements) >= 8:
            break
    return items + supplements, supplements


def _merge_work(item: dict[str, Any], work: dict[str, Any]) -> None:
    if not item.get("doi") and work.get("doi"):
        item["doi"] = work["doi"]
        item["doi_url"] = work.get("doi_url") or f"https://doi.org/{work['doi']}"
    if not item.get("authors") and work.get("authors"):
        item["authors"] = work["authors"]
    if not item.get("abstract") and work.get("abstract"):
        item["abstract"] = work["abstract"]
    for key in ("volume", "issue", "pages", "online_date"):
        if not item.get(key) and work.get(key):
            item[key] = work[key]
    if work.get("citation_count", 0) > item.get("citation_count", 0):
        item["citation_count"] = work["citation_count"]
    if work.get("published_date") and not item.get("published_date"):
        item["published_date"] = work["published_date"]
        item["published_precision"] = work.get("published_precision", "day")
    if not item.get("fingerprint"):
        item["fingerprint"] = make_fingerprint(item.get("doi", ""), item.get("pii", ""), item["title"], 0)


def _openalex_abstract(inverted: dict[str, list[int]] | None) -> str:
    if not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, indexes in inverted.items():
        for index in indexes:
            positions[index] = word
    return " ".join(positions[i] for i in sorted(positions))


def enrich_openalex(items: list[dict[str, Any]]) -> int:
    candidates = {}
    for item in items:
        doi = (item.get("doi") or "").lower()
        if doi and not item.get("abstract"):
            candidates[doi] = item
    if not candidates:
        return 0
    enriched = 0
    dois = list(candidates)
    for start in range(0, len(dois), 25):
        chunk = dois[start:start + 25]
        flt = "doi:" + "|".join(f"https://doi.org/{d}" for d in chunk)
        params = {
            "filter": flt,
            "per-page": "50",
            "select": "doi,abstract_inverted_index,cited_by_count,open_access",
        }
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        try:
            payload = json.loads(http_get(url, accept="application/json", timeout=18, retries=1).decode("utf-8", "replace"))
        except Exception:
            continue
        for work in payload.get("results", []):
            doi = extract_doi(work.get("doi") or "")
            item = candidates.get(doi)
            if not item:
                continue
            abstract = _openalex_abstract(work.get("abstract_inverted_index"))
            if abstract and len(abstract) > len(item.get("abstract") or ""):
                item["abstract"] = abstract[:6000]
                enriched += 1
            if work.get("cited_by_count") and work["cited_by_count"] > item.get("citation_count", 0):
                item["citation_count"] = work["cited_by_count"]
            if (work.get("open_access") or {}).get("is_oa"):
                item["open_access"] = True
        time.sleep(0.15)
    return enriched


def fetch_openalex_citations(papers: list[dict[str, Any]], update_fn, should_stop=None) -> tuple[int, int]:
    """按 DOI 批量查询 OpenAlex 的被引数，用 update_fn(paper_id, count) 回写。

    返回 (成功更新篇数, 失败篇数)。update_fn 返回 True 表示数值有变化。
    """
    updated = 0
    failed = 0
    by_doi: dict[str, dict[str, Any]] = {}
    for paper in papers:
        doi = (paper.get("doi") or "").strip().lower()
        if doi:
            by_doi[doi] = paper
    dois = sorted(by_doi)
    for start in range(0, len(dois), 25):
        if should_stop and should_stop():
            break
        chunk = dois[start:start + 25]
        flt = "doi:" + "|".join(f"https://doi.org/{d}" for d in chunk)
        params = {
            "filter": flt,
            "per-page": "50",
            "select": "doi,cited_by_count",
        }
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        try:
            payload = json.loads(http_get(url, accept="application/json", timeout=18, retries=1).decode("utf-8", "replace"))
        except Exception:
            failed += len(chunk)
            time.sleep(0.4)
            continue
        found = set()
        for work in payload.get("results", []):
            doi = extract_doi(work.get("doi") or "")
            paper = by_doi.get(doi)
            if not paper:
                continue
            found.add(doi)
            try:
                if update_fn(int(paper["id"]), int(work.get("cited_by_count") or 0)):
                    updated += 1
            except Exception:
                failed += 1
        failed += len(chunk) - len(found)
        time.sleep(0.15)
    return updated, failed
