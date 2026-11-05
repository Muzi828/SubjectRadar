import unittest
import base64
import json
from unittest.mock import patch
from app.article_metadata import parse_page, parse_paperopen, enrich_and_audit, safe_url, _validate_network_url, acceptance_result

HTML = '''<html lang="zh"><head>
<meta name="citation_title" content="安全研究"><meta name="citation_title_en" content="Safety study">
<meta name="citation_author" content="张三"><meta name="citation_author" content="李四">
<meta name="citation_author_en" content="Zhang San"><meta name="citation_author_en" content="Li Si">
<meta name="citation_author_institution" content="安全大学"><meta name="citation_funding" content="科学基金 123">
<meta name="citation_doi" content="10.1234/test"><meta name="citation_abstract" content="完整摘要。">
<meta name="citation_keywords" content="安全；研究"><meta name="citation_pdf_url" content="/paper.pdf">
<meta name="citation_reference" content="THIS IS ANOTHER ARTICLE">
</head><body><div id="abstract_en">Abstract: Full abstract describing the article methods and findings.</div>
<a href="/export.ris">RIS 下载</a><a href="javascript:alert(1)">PDF</a></body></html>'''


class MetadataTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("app.article_metadata.verify_pdf_link", return_value={"status": "passed", "bytes_read": 1024})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bilingual_and_links(self):
        data, evidence = parse_page(HTML, "https://example.com/article")
        self.assertEqual(data["authors_zh"], ["张三", "李四"])
        self.assertEqual(data["title_en"], "Safety study")
        self.assertEqual(data["abstract_en"], "Full abstract describing the article methods and findings.")
        self.assertEqual(data["pdf_url"], "https://example.com/paper.pdf")
        self.assertEqual(data["funding_zh"], "科学基金 123")
        self.assertNotIn("citation_text", data)
        self.assertEqual({x["format"] for x in data["download_links"]}, {"RIS", "PDF"})
        self.assertEqual(evidence["title_zh"], "meta:citation_title")

    @patch("app.article_metadata.fetch_page", return_value=(HTML, "https://example.com/article"))
    def test_audit_missing_and_conflict(self, fetch):
        result = enrich_and_audit({"url": "https://example.com/article", "title": "安全研究", "funding": "其他基金"})
        self.assertEqual(result["audit"]["status"], "conflict")
        self.assertIn("funding_zh", result["audit"]["conflicts"])
        self.assertIn("affiliations_en", result["audit"]["missing"])
        self.assertEqual(result["audit"]["fields"]["title_zh"]["status"], "verified")

    @patch("app.article_metadata.fetch_page", return_value=(HTML, "https://example.com/article"))
    def test_wrong_identity_not_applied(self, fetch):
        result = enrich_and_audit({"url": "https://example.com/article", "doi": "10.9999/other"})
        self.assertEqual(result["metadata"], {})
        self.assertEqual(result["audit"]["status"], "conflict")

    @patch("app.article_metadata.fetch_page", side_effect=TimeoutError("timeout"))
    def test_failure_not_reported_missing(self, fetch):
        result = enrich_and_audit({"url": "https://example.com/article"})
        self.assertEqual(result["metadata"], {})
        self.assertEqual(result["audit"]["status"], "unavailable")
        self.assertFalse(result["audit"]["missing"])

    def test_reject_unsafe_links(self):
        for url in ("file:///tmp/a", "javascript:alert(1)", "http://127.0.0.1/x", "http://[::1]/", "http://user:pass@example.com"):
            self.assertEqual(safe_url(url), "")

    @patch("app.article_metadata.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 80))])
    def test_private_dns_rejected(self, lookup):
        with self.assertRaises(ValueError):
            _validate_network_url("https://example.com")

    def test_jsonld(self):
        raw = '''<script type="application/ld+json">{"@type":"ScholarlyArticle","headline":"A study","abstract":"Full abstract","author":[{"name":"A Smith","affiliation":{"name":"Test University"}}],"funder":{"name":"Grant ABC"}}</script>'''
        data, _ = parse_page(raw, "https://example.com")
        self.assertEqual(data["affiliations_en"], "Test University")
        self.assertEqual(data["funding_en"], "Grant ABC")

    def test_embedded_bilingual_record(self):
        payload = {"titleCn": "研究", "titleEn": "Study", "authors": [{"authorNameCn": "张三", "authorNameEn": "Zhang San"}], "affiliations": [{"addressCn": "大学", "addressEn": "University"}], "citationCn": "原文引用", "fundPrjs": []}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        data, _ = parse_page("<script>var article_meta_data='" + encoded + "';</script><a href='https://mathjax.xml-journal.net'>MathJax</a>", "https://example.com")
        self.assertEqual(data["affiliations_en"], "University")
        self.assertEqual(data["authors_en"], ["Zhang San"])
        self.assertNotIn("download_links", data)
        self.assertNotIn("funding", data)

    @patch("app.article_metadata.fetch_page", return_value=(HTML, "https://example.com/article"))
    def test_wrong_title_not_applied(self, fetch):
        result = enrich_and_audit({"url": "https://example.com/article", "title": "关于完全不同的人工智能论文"})
        self.assertEqual(result["metadata"], {})
        self.assertEqual(result["audit"]["status"], "conflict")

    @patch("app.article_metadata.fetch_page", return_value=(HTML, "https://example.com/article"))
    def test_new_value_is_extracted(self, fetch):
        result = enrich_and_audit({"url": "https://example.com/article"})
        self.assertEqual(result["audit"]["fields"]["title_zh"]["status"], "extracted")

    def test_cssjj_heading_not_abstract(self):
        raw = '<a id="abstract">摘要/Abstract</a><p><strong>Abstract: </strong><p id="p001">This study investigates safety through an extensive experimental methodology.</p></p>'
        data, _ = parse_page(raw, "https://www.cssjj.com.cn/CN/123")
        self.assertTrue(data["abstract_en"].startswith("This study"))
        self.assertNotEqual(data["abstract_en"], "/Abstract")

    def test_paperopen_evidence_and_identity(self):
        row = {"IsSuccess": True, "Data": {"ArticleID": 12, "Title": "测试论文", "TitleE": "Test paper", "AuthorsList": "张三，李四", "Unit_EngLish": "Test university", "MemoryEN": "Received: 2026-01-01\nFund program: Grant 123\nAuthor biography: Test", "DOI": None}}
        data, evidence = parse_paperopen(row, "http://aqsckj.paperopen.com/#/digest?ArticleID=12", "12")
        self.assertEqual(data["funding_en"], "Grant 123")
        self.assertEqual(evidence["affiliations_en"], "api:Data.Unit_EngLish")
        self.assertNotIn("doi", data)
        with self.assertRaises(ValueError):
            parse_paperopen(row, "http://aqsckj.paperopen.com/", "13")

    def test_cast_pdf_button_and_no_script_metadata(self):
        raw = '<div class="rich_main_con_left_title">Article title</div><div class="affiliations"><script>javascriptNotAnInstitution()</script></div><a onclick="openPDFLink(event,\'https://castjournals.cast.org.cn/CN/PDF/123\')">PDF</a>'
        data, _ = parse_page(raw, "https://castjournals.cast.org.cn/CN/123")
        self.assertEqual(data["pdf_url"], "https://castjournals.cast.org.cn/CN/PDF/123")
        self.assertNotIn("affiliations_en", data)

    def test_acceptance_needs_actual_link_check_not_second_language(self):
        data = {"title": "Title", "authors": ["Author"], "abstract": "Abstract", "pdf_url": "https://example.com/a.pdf"}
        self.assertEqual(acceptance_result(data, {"status": "partial"})["status"], "pending")
        audit = {"status": "partial", "link_checks": {"pdf": {"status": "passed"}}}
        self.assertEqual(acceptance_result(data, audit)["status"], "passed")
        self.assertEqual(acceptance_result(data, audit, True)["status"], "failed")
        self.assertEqual(acceptance_result(data, {**audit, "conflicts": ["title"]})["status"], "failed")
        self.assertEqual(acceptance_result({}, {"status": "unavailable"})["status"], "blocked")

    def test_affiliation_section_heading_is_not_an_institution(self):
        data, _ = parse_page('<div class="affiliations">Affiliations</div>', 'https://example.com/article')
        self.assertNotIn('affiliations_en', data)

    def test_springer_citation_export_excludes_references(self):
        html = '<a href="https://citation-needed.springer.com/v2/references/10.1007/test?format=refman&amp;flavour=citation">Download citation</a><a href="https://citation-needed.springer.com/v2/references/10.1007/test?format=refman&amp;flavour=references">Download references</a>'
        data, _ = parse_page(html, "https://link.springer.com/article/test")
        self.assertEqual(len(data["download_links"]), 1)
        self.assertEqual(data["download_links"][0]["format"], "RIS")


if __name__ == "__main__":
    unittest.main()
