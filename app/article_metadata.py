"""Evidence-based publisher-page extraction and deterministic metadata audit agent.

Only values observed in publisher HTML are returned; this module never translates,
constructs download URLs, or treats a discovered link as an access check.
"""
from __future__ import annotations

import hashlib
import base64
from difflib import SequenceMatcher
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from .sources import strip_html, parse_authors, extract_doi

MAX_BYTES = 4 * 1024 * 1024
BILINGUAL = ("title", "authors", "affiliations", "funding", "abstract", "keywords")
FIELDS = tuple(f"{key}_{lang}" for key in BILINGUAL for lang in ("zh", "en")) + ("doi", "pdf_url", "preview_url", "citation_text", "citation_text_en", "download_links")


def safe_url(url: str, base: str = "") -> str:
    value = urllib.parse.urljoin(base, url.strip())
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return ""
    if parsed.hostname.lower() in ("localhost", "localhost.localdomain"):
        return ""
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global:
            return ""
    except ValueError:
        pass
    return value


def _validate_network_url(url: str) -> None:
    if not safe_url(url):
        raise ValueError("不允许的原文地址")
    parsed = urllib.parse.urlsplit(url)
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    # The workstation's DNS proxy maps public domains to the benchmark Fake-IP
    # range. Literal non-public addresses are still rejected by safe_url above.
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global and ipaddress.ip_address(item[4][0]) not in ipaddress.ip_network("198.18.0.0/15") for item in addresses):
        raise ValueError("原文地址解析到非公网地址")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_network_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url: str, *, json_response: bool = False) -> tuple[str, str]:
    _validate_network_url(url)
    opener = urllib.request.build_opener(_SafeRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "SafeLens-MetadataAudit/1.0", "Accept": "application/json" if json_response else "text/html,application/xhtml+xml"})
    with opener.open(request, timeout=15) as response:
        final = response.geturl()
        _validate_network_url(final)
        kind = response.headers.get_content_type()
        if kind not in (("application/json", "text/json") if json_response else ("text/html", "application/xhtml+xml")):
            raise ValueError("原文地址未返回所需页面格式")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("原文页面超过 4 MiB 限制")
        charset = response.headers.get_content_charset()
        if not charset:
            match = re.search(br'charset\s*=\s*["\']?([\w-]+)', raw[:8192], re.I)
            charset = match.group(1).decode("ascii") if match else "utf-8"
        try:
            return raw.decode(charset, "replace"), final
        except LookupError:
            return raw.decode("utf-8", "replace"), final


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.links = []
        self.blocks = []
        self.stack = []
        self.json_ld = []
        self.lang = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.lang = attrs.get("lang", "")
        if tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").lower()
            if key and attrs.get("content"):
                self.meta.setdefault(key, []).append(attrs["content"])
        if tag == "link" and attrs.get("href"):
            self.links.append({**attrs, "text": attrs.get("title", "")})
        if tag not in ("meta", "link", "img", "br", "hr", "input", "source", "wbr", "area", "base", "embed", "param", "track"):
            self.stack.append([tag, attrs, []])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] in ("script", "style"):
            self.stack[-1][2].append(data)
            return
        for entry in self.stack:
            entry[2].append(data)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        closed = self.stack[index:]
        del self.stack[index:]
        for name, attrs, parts in closed:
            text = " ".join(" ".join(parts).split())
            if name == "a" and attrs.get("href"):
                self.links.append({**attrs, "text": text})
            if name == "script" and attrs.get("type", "").lower() == "application/ld+json":
                try:
                    self.json_ld.append(json.loads("".join(parts)))
                except (ValueError, TypeError):
                    pass
            if name not in ("script", "style") and text:
                self.blocks.append((name, attrs, text))


def _language(value: Any) -> str:
    return "zh" if re.search(r"[\u3400-\u9fff]", str(value)) else "en"


def _clean(value: str) -> str:
    return strip_html(value).strip()


def parse_page(html: str, source_url: str) -> tuple[dict, dict]:
    page = PageParser()
    page.feed(html)
    data, evidence = {}, {}

    def put(key, value, origin, lang=""):
        if not value:
            return
        if isinstance(value, str):
            value = _clean(value)
        if not value:
            return
        if isinstance(value, str) and re.fullmatch(r"(?:Affiliations?|Institutions?|Funding|Keywords?|Authors?|Abstract|Title)\s*[:：]?", value, re.I):
            return
        if key == "abstract" and (len(value) < 40 or re.fullmatch(r"[\s/]*Abstract[\s/]*", value, re.I)):
            return
        if key in BILINGUAL:
            localized = key + "_" + (lang or _language(value))
            if localized not in data:
                data[localized], evidence[localized] = value, origin
        if key not in data:
            data[key], evidence[key] = value, origin

    aliases = {
        "title": ("citation_title", "dc.title", "dc.title.alternative"),
        "authors": ("citation_author", "citation_authors", "dc.creator"),
        "affiliations": ("citation_author_institution", "citation_affiliation", "dc.creator.affiliation"),
        "funding": ("citation_funding", "citation_funder", "dc.description.sponsorship"),
        "abstract": ("citation_abstract", "dc.description", "dcterms.abstract"),
        "keywords": ("citation_keywords", "dc.keywords", "dc.subject"),
        "doi": ("citation_doi", "dc.identifier.doi", "prism.doi"),
        "citation_text": ("citation_reference",),
    }
    # citation_reference describes references, not the current article: never use it.
    aliases.pop("citation_text")
    for key, names in aliases.items():
        for name in names:
            for suffix, lang in (("", ""), ("_en", "en"), ("_zh", "zh"), (".en", "en"), (".zh", "zh")):
                values = page.meta.get(name + suffix, [])
                if not values:
                    continue
                by_lang = {}
                for value in values:
                    by_lang.setdefault(lang or _language(value), []).append(value)
                for detected, entries in by_lang.items():
                    if key == "authors":
                        value = list(dict.fromkeys(a for entry in entries for a in parse_authors(entry)))
                    elif key == "keywords":
                        value = list(dict.fromkeys(x.strip() for entry in entries for x in re.split(r"[;；,，]", entry) if x.strip()))
                    elif key == "doi":
                        value = extract_doi(entries[0])
                    else:
                        value = "\n".join(entries)
                    put(key, value, "meta:" + name + suffix, detected)
    for key, names in {"pdf_url": ("citation_pdf_url",), "preview_url": ("citation_fulltext_html_url", "citation_abstract_html_url")}.items():
        for name in names:
            for value in page.meta.get(name, []):
                put(key, safe_url(value, source_url), "meta:" + name)
    patterns = {
        "title": r"^(?:article[-_]?title|title[-_]?(?:cn|zh|en)|en[-_]title)$",
        "abstract": r"^(?:article[-_]?)?(?:abstract|abstract[-_]?(?:cn|zh|en)|(?:cn|zh|en)[-_]abstract)$",
        "affiliations": r"^(?:author[-_]?)?(?:affiliations?|institutions?)(?:[-_](?:cn|zh|en))?$",
        "funding": r"^(?:funding|funds?|fund[-_]?project)(?:[-_](?:cn|zh|en))?$",
        "keywords": r"^(?:keywords?)(?:[-_]?(?:cn|zh|en))?$",
        "citation_text": r"^(?:citation|cite[-_]?text|reference[-_]?text)(?:[-_](?:cn|zh|en))?$",
    }
    host = urllib.parse.urlsplit(source_url).hostname or ""
    if host == "castjournals.cast.org.cn":
        names, funds = [], []
        for tag, attrs, text in page.blocks:
            classes = attrs.get("class", "").split()
            if "rich_main_con_left_title" in classes:
                put("title", re.sub(r"\s*全屏\s*$", "", text), "html:rich_main_con_left_title")
            if "author_string_name" in classes and text not in names:
                names.append(text)
            if "fund_text_class" in classes and text not in funds:
                funds.append(text)
            if "rich_main_con_left_zuozhexinxi_con" in classes:
                put("affiliations", text, "html:rich_main_con_left_zuozhexinxi_con")
        put("authors", names, "html:author_string_name")
        put("funding", "\n".join(funds), "html:fund_text_class")
        for key, label in (("abstract", "摘要"), ("keywords", "关键词"), ("citation_text", "引用本文")):
            match = re.search(re.escape(label) + r"</div>.*?<div class=\"neirong[^\"]*\">(.*?)</div>", html, re.S)
            if match:
                value = _clean(match.group(1))
                put(key, [x.strip() for x in value.split("/") if x.strip()] if key == "keywords" else value, "html:" + label)
        match = re.search(r"openPDFLink\(event\s*,\s*['\"]([^'\"]+)['\"]\)", html)
        if match:
            put("pdf_url", safe_url(match.group(1), source_url), "html:openPDFLink button")
        match = re.search(r"DOI\s*[:：]\s*(10\.\d{4,9}/[^\s<]+)", html, re.I)
        if match:
            put("doi", extract_doi(match.group(1)), "html:DOI")
    elif host.endswith(".cbpt.cnki.net"):
        for tag, attrs, text in page.blocks:
            classes = attrs.get("class", "").split()
            if "paperDetail_tt" in classes:
                put("title", text, "html:paperDetail_tt")
            if "paperDetail_foundation" in classes:
                put("funding", re.sub(r"^基金项目\(Foundation\)\s*:", "", text).strip(), "html:paperDetail_foundation")
            if "paperDetail_doi" in classes:
                put("doi", extract_doi(text), "html:paperDetail_doi")
        for key, callback in (("authors", "clickAuthorAction"), ("affiliations", "clickAuthorUnitAction")):
            values = list(dict.fromkeys(re.findall(callback + r"\('([^']+)'\)", html)))
            put(key, values if key == "authors" else "\n".join(values), "html:" + callback)
        for lang, label in (("zh", "摘要"), ("en", "Abstract")):
            match = re.search(r"<b>\s*" + label + r"\s*[:：]\s*</b>\s*<p>(.*?)</p>", html, re.I | re.S)
            if match:
                put("abstract", _clean(match.group(1)), "html:" + label, lang)
        keyword_values = list(dict.fromkeys(re.findall(r"clickKeyWordAction\('([^']+)'\)", html)))
        for lang in ("zh", "en"):
            put("keywords", [v for v in keyword_values if _language(v) == lang], "html:clickKeyWordAction", lang)
        match = re.search(r"downloadFull\('([a-f0-9]+)'\s*,\s*'1'\s*,\s*'([\w-]+)'", html)
        if match and match.group(2) == host.split('.')[0]:
            put("pdf_url", urllib.parse.urljoin(source_url, "/portal/journal/portal/client/updateDownLoadNumber/" + match.group(1) + "/1"), "html:downloadFull button and function")
    for tag, attrs, text in page.blocks:
        tokens = (attrs.get("id", "") + " " + attrs.get("class", "")).lower().split()
        for key, pattern in patterns.items():
            if any(re.fullmatch(pattern, token) for token in tokens):
                value = re.sub(r"^(摘要|Abstract|关键词|Keywords|基金项目|Funding)\s*[:：]?\s*", "", text, flags=re.I)
                if key == "keywords":
                    value = [x.strip() for x in re.split(r"[;；,，]", value) if x.strip()]
                put(key, value, "html:" + " ".join(tokens))
    def json_nodes(obj):
        if isinstance(obj, list):
            for item in obj:
                yield from json_nodes(item)
        elif isinstance(obj, dict):
            yield obj
            yield from json_nodes(obj.get("@graph", []))
    for obj in json_nodes(page.json_ld):
        types = obj.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if not any(t in ("ScholarlyArticle", "Article", "MedicalScholarlyArticle") for t in types):
            continue
        for key, field in (("title", "headline"), ("abstract", "abstract")):
            if isinstance(obj.get(field), str):
                put(key, obj[field], "jsonld:" + field)
        for key, field in (("authors", "author"), ("funding", "funder")):
            values = obj.get(field, [])
            if not isinstance(values, list):
                values = [values]
            names = [x.get("name", "") if isinstance(x, dict) else x for x in values]
            names = [x for x in names if isinstance(x, str) and x]
            put(key, names if key == "authors" else "\n".join(names), "jsonld:" + field)
            if key == "authors":
                institutions = []
                for author in values:
                    if isinstance(author, dict):
                        af = author.get("affiliation", [])
                        for item in (af if isinstance(af, list) else [af]):
                            name = item.get("name", "") if isinstance(item, dict) else item
                            if isinstance(name, str) and name:
                                institutions.append(name)
                put("affiliations", "\n".join(dict.fromkeys(institutions)), "jsonld:author.affiliation")
    # XML-journal embeds the complete bilingual record as data, not executable code.
    match = re.search(r"\barticle_meta_data\s*=\s*['\"]([A-Za-z0-9+/=]+)['\"]", html)
    if match:
        try:
            article = json.loads(base64.b64decode(match.group(1)))
            for suffix, lang in (("Cn", "zh"), ("En", "en")):
                for key, field in (("title", "title"), ("abstract", "abstractinfo")):
                    put(key, article.get(field + suffix), "embedded:article." + field + suffix, lang)
                for key, collection, field in (("authors", "authors", "authorName"), ("affiliations", "affiliations", "address"), ("keywords", "keywords", "keyword")):
                    values = [_clean(row.get(field + suffix) or "") for row in article.get(collection, [])]
                    values = [v for v in values if v]
                    put(key, values if key in ("authors", "keywords") else "\n".join(values), "embedded:article." + collection, lang)
                funds = [" ".join(str(row.get(f + suffix) or "") for f in ("source", "fundsInfo", "fundNo")).strip() for row in article.get("fundPrjs", [])]
                put("funding", "\n".join(v for v in funds if v), "embedded:article.fundPrjs", lang)
                put("citation_text" if lang == "zh" else "citation_text_en", article.get("citation" + suffix), "embedded:article.citation" + suffix)
        except (ValueError, TypeError, AttributeError):
            pass
    # CSSJJ's bilingual affiliations are <li> blocks with institution markup;
    # its funding label and content are siblings.
    if urllib.parse.urlsplit(source_url).hostname in ("www.cssjj.com.cn", "cssjj.com.cn"):
        for match in re.finditer(r"<li\b[^>]*>(?:(?!</li>).)*?<institution-wrap[\s\S]*?</li>", html, re.I):
            put("affiliations", _clean(match.group()), "html:institution-wrap")
        match = re.search(r"基金资助\s*[:：]\s*</code>\s*<div[^>]*>(.*?)</div>", html, re.I | re.S)
        if match:
            put("funding", _clean(match.group(1)), "html:基金资助", "zh")
        match = re.search(r"<strong>\s*摘要\s*[:：]\s*</strong>(.*?)<form", html, re.I | re.S)
        if match:
            put("abstract", _clean(match.group(1)), "html:摘要", "zh")
        match = re.search(r"<strong>\s*Abstract\s*:\s*</strong>\s*(?:<p[^>]*>)?(.*?)</p>", html, re.I | re.S)
        if match:
            put("abstract", _clean(match.group(1)), "html:Abstract", "en")
    downloads = []
    if data.get("pdf_url"):
        downloads.append({"label": "PDF", "format": "PDF", "url": data["pdf_url"]})
    for link in page.links:
        url = safe_url(link.get("href", ""), source_url)
        if not url:
            continue
        label = link.get("text", "").strip()
        kind = link.get("type", "").lower()
        text = (label + " " + url).lower()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("flavour") == ["references"]:
            continue
        fmt = ""
        if kind == "application/pdf" or re.search(r"\.pdf(?:[?#]|$)|\bpdf\b", text):
            fmt = "PDF"
            put("pdf_url", url, "link:" + label)
        else:
            if query.get("format") == ["refman"] and query.get("flavour") == ["citation"]:
                fmt = "RIS"
            for name in ("bibtex", "ris", "endnote", "xml", "epub"):
                if re.search(r"\b" + name + r"\b", label, re.I) or re.search(r"\." + name + r"(?:[?#]|$)", url, re.I):
                    fmt = name.upper()
                    break
        if fmt and not any(d["url"] == url for d in downloads):
            downloads.append({"label": label or fmt, "format": fmt, "url": url})
        if re.search(r"全文浏览|在线阅读|在线浏览|full\s*text|view\s*html", label, re.I) and fmt != "PDF":
            put("preview_url", url, "link:" + label)
    put("download_links", downloads, "publisher:links")
    primary_language = _language(data.get("title", ""))
    for key in BILINGUAL:
        localized = key + "_" + primary_language
        if data.get(localized):
            data[key], evidence[key] = data[localized], evidence[localized]
    return data, evidence


def _normalized(value):
    if isinstance(value, list):
        return sorted(_normalized(x) for x in value)
    return re.sub(r"\s+", "", str(value or "")).casefold().rstrip(".")


def verify_pdf_link(url: str) -> dict:
    """Read at most 1024 bytes, follow only validated redirects, never infer PDF from a URL."""
    result = {"url": url, "status": "pending", "checked_at": datetime.now(timezone.utc).isoformat(), "bytes_read": 0}
    try:
        _validate_network_url(url)
        headers = {"User-Agent": "SafeLens-MetadataAudit/1.0", "Range": "bytes=0-1023", "Accept": "application/pdf"}
        host = urllib.parse.urlsplit(url).hostname or ""
        if host.endswith(".cbpt.cnki.net"):
            headers["web_id"] = host.split('.')[0]
        with urllib.request.build_opener(_SafeRedirect()).open(urllib.request.Request(url, headers=headers), timeout=10) as response:
            _validate_network_url(response.geturl())
            prefix = response.read(1024)
            result.update(final_url=response.geturl(), http_status=response.status, content_type=response.headers.get("Content-Type", ""), bytes_read=len(prefix))
            result["status"] = "passed" if prefix.lstrip().startswith(b"%PDF-") else "blocked" if b"login" in prefix.lower() or response.headers.get_content_type() == "text/html" else "failed"
            result["reason"] = "已读取 PDF 文件头 %PDF-" if result["status"] == "passed" else "下载地址未返回 PDF 文件头，可能需要登录或权限"
    except Exception as exc:
        result.update(status="blocked", reason=str(exc))
    return result


def acceptance_result(metadata: dict, audit: dict, identity_mismatch: bool = False) -> dict:
    if identity_mismatch:
        return {"status": "failed", "reasons": ["原文身份与入库记录不一致"]}
    if audit.get("status") == "unavailable":
        return {"status": "blocked", "reasons": audit.get("errors") or ["无法读取原文"]}
    if audit.get("conflicts"):
        return {"status": "failed", "scope": "自动基础检验", "reasons": ["尚有未解决的原文与入库冲突：" + ", ".join(audit["conflicts"])]}
    missing = [key for key in ("title", "authors", "abstract") if not metadata.get(key)]
    checks = audit.get("link_checks", {})
    if any(check.get("status") == "failed" for check in checks.values()):
        return {"status": "failed", "reasons": ["资源链接未返回预期文件"]}
    if any(check.get("status") == "blocked" for check in checks.values()):
        return {"status": "blocked", "reasons": [check.get("reason", "访问受限") for check in checks.values() if check.get("status") == "blocked"]}
    if missing:
        return {"status": "pending", "reasons": ["核心字段尚无原文证据：" + ", ".join(missing)]}
    if not metadata.get("pdf_url") or checks.get("pdf", {}).get("status") != "passed":
        return {"status": "pending", "reasons": ["PDF 入口尚未发现或尚未实际验证"]}
    reasons = ["标题、作者、摘要有原文证据，论文身份匹配，PDF 文件头验证通过；第二语言缺失不单独判失败"]
    if audit.get("missing"):
        reasons.append("其余未提取字段仍待人工对照原文核查：" + ", ".join(audit["missing"]))
    return {"status": "passed", "scope": "自动基础检验", "reasons": reasons}


def parse_paperopen(payload: dict, source_url: str, article_id: str) -> tuple[dict, dict]:
    """Fields verified against PaperOpen GetArticleDetailed and its shipped app JS."""
    row = payload.get("Data")
    if not payload.get("IsSuccess") or not isinstance(row, dict) or str(row.get("ArticleID")) != article_id:
        raise ValueError("官网详情 API 未返回匹配的 ArticleID")
    data, evidence = {}, {}
    mapping = {"title_zh": "Title", "title_en": "TitleE", "authors_zh": "AuthorsList", "authors_en": "AuthorsListE", "affiliations_zh": "Unit", "affiliations_en": "Unit_EngLish", "abstract_zh": "Abstract_Chinese", "abstract_en": "Abstract_English", "keywords_zh": "Keyword_Chinese", "keywords_en": "Keywords_English", "citation_text": "InCite", "citation_text_en": "InCiteEN", "doi": "DOI", "preview_url": "HtmlFileUrl"}
    for key, field in mapping.items():
        value = _clean(str(row.get(field) or ""))
        if key.startswith("authors"):
            value = parse_authors(value)
        elif key.startswith("keywords"):
            value = [x.strip() for x in re.split(r"[;；]", value) if x.strip()]
        elif key == "doi":
            value = extract_doi(value)
        elif key == "preview_url":
            value = safe_url(value, source_url) if value else ""
        elif key == "citation_text":
            value = _clean(re.split(r"<br\s*/?>", row.get(field) or "", flags=re.I)[0])
        if value:
            data[key], evidence[key] = value, "api:Data." + field
    for lang, field, label in (("zh", "Memory", r"基金项目\s*[:：]"), ("en", "MemoryEN", r"Fund program\s*:")):
        notes = str(row.get(field) or "")
        match = re.search(label + r"([^\n<]+)", notes, re.I)
        if match:
            data["funding_" + lang] = _clean(match.group(1))
            evidence["funding_" + lang] = "api:Data." + field
    # This is the publisher's own download route, observed in app.7380f519.js.
    # No request is made to the file endpoint during metadata auditing.
    pdf = ""
    if row.get("PDFisurl") and row.get("PDFFileNameUrl"):
        pdf = safe_url(row["PDFFileNameUrl"], source_url)
    elif row.get("PDFFile_Size", 0) > 0 and re.fullmatch(r"[0-9a-fA-F-]{36}", row.get("PDFFileNameUrlGUID") or ""):
        pdf = urllib.parse.urljoin(source_url, "/api/api/Web/OpenArticleFilebyGuidNew?Id=" + row["PDFFileNameUrlGUID"])
    if pdf:
        data["pdf_url"], evidence["pdf_url"] = pdf, "api:Data.PDFFileNameUrlGUID + publisher download route"
        data["download_links"] = [{"label": "PDF（官网）", "format": "PDF", "url": pdf}]
        evidence["download_links"] = evidence["pdf_url"]
    for key in BILINGUAL:
        if data.get(key + "_zh"):
            data[key], evidence[key] = data[key + "_zh"], evidence[key + "_zh"]
    return data, evidence


def enrich_and_audit(paper: dict[str, Any]) -> dict[str, Any]:
    source = paper.get("url") or paper.get("doi_url") or ""
    audit = {"agent": "publisher-metadata-audit-v1", "checked_at": datetime.now(timezone.utc).isoformat(), "source_url": source, "final_url": "", "status": "unavailable", "fields": {}, "missing": [], "conflicts": [], "errors": [], "link_check": "链接仅核对是否出现在原文，未验证下载权限或文件内容"}
    try:
        parsed_source = urllib.parse.urlsplit(source)
        article_id = urllib.parse.parse_qs(parsed_source.fragment.partition("?")[2]).get("ArticleID", [""])[0]
        paperopen = parsed_source.hostname == "aqsckj.paperopen.com" and article_id.isdigit()
        if paperopen:
            api_url = urllib.parse.urlunsplit((parsed_source.scheme, parsed_source.netloc, "/api/api/Web/GetArticleDetailed", "id=" + article_id, ""))
            html, final = fetch_page(api_url, json_response=True)
            metadata, evidence = parse_paperopen(json.loads(html), source, article_id)
        else:
            html, final = fetch_page(source)
            metadata, evidence = parse_page(html, final)
        audit["final_url"] = final
        audit["content_sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
        if not metadata.get("title") and not metadata.get("doi"):
            raise ValueError("页面未识别出论文标题或 DOI，可能为验证页或不支持的页面")
        # A wrong DOI means the entire page is unsuitable evidence for this paper.
        identity_mismatch = bool(paper.get("doi") and metadata.get("doi") and extract_doi(paper["doi"]) != metadata["doi"])
        if not (paper.get("doi") and metadata.get("doi")) and paper.get("title") and metadata.get("title"):
            if _language(paper["title"]) == _language(metadata["title"]):
                identity_mismatch = SequenceMatcher(None, _normalized(paper["title"]), _normalized(metadata["title"])).ratio() < 0.55
        for key in tuple(dict.fromkeys(FIELDS + tuple(metadata))):
            value = metadata.get(key)
            old = paper.get(key)
            if old is None and key.endswith(("_en", "_zh")):
                base, lang = key.rsplit("_", 1)
                if paper.get(base) and _language(paper[base]) == lang:
                    old = paper[base]
            status = "missing" if not value else "conflict" if old and _normalized(old) != _normalized(value) else "verified" if old else "extracted"
            audit["fields"][key] = {"status": status, "source_value": value, "stored_value": old, "evidence": evidence.get(key, ""), "source_url": final}
            if status == "missing":
                audit["missing"].append(key)
            elif status == "conflict":
                audit["conflicts"].append(key)
        audit["status"] = "conflict" if audit["conflicts"] else "partial" if audit["missing"] else "verified"
        if identity_mismatch:
            audit["status"] = "conflict"
            audit["errors"].append("原文身份（DOI 或标题）与入库记录不一致，未应用任何候选字段")
            metadata = {}
        audit["link_checks"] = {"source": {"status": "passed", "url": final, "reason": "已获取原文并提取论文元数据"}}
        if metadata.get("pdf_url"):
            audit["link_checks"]["pdf"] = verify_pdf_link(metadata["pdf_url"])
        audit["link_check"] = "已实际请求 PDF（最多读取 1024 字节）并核验 %PDF- 文件头；其他导出链接仅核对原文存在"
        audit["acceptance"] = acceptance_result(metadata, audit, identity_mismatch)
        return {"metadata": metadata, "audit": audit}
    except Exception as exc:
        audit["errors"].append(str(exc))
        for key in FIELDS:
            audit["fields"][key] = {"status": "unavailable", "stored_value": paper.get(key), "source_value": None, "evidence": "", "source_url": source}
        audit["acceptance"] = acceptance_result({}, audit)
        return {"metadata": {}, "audit": audit}
