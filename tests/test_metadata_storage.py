import json
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, db, sync, server


class MetadataStorageTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "_tmp"
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        path = Path(self.temp.name)
        self.patches = [patch.object(config, name, value) for name, value in
                        (("DATA_DIR", path), ("LOG_DIR", path / "logs"), ("DB_PATH", path / "test.db"))]
        for p in self.patches:
            p.start()
        db.init_db()
        self.item = {"fingerprint": "test", "title": "Stored title", "journal_id": db.list_journals()[0]["id"],
                     "url": "https://example.com/paper", "authors": ["Alice"]}
        self.pid, _ = db.upsert_paper(self.item)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_migration_idempotent_and_preserves_reader_data(self):
        db.init_db()
        db.update_paper(self.pid, {"notes": "Keep me", "starred": True})
        result = {"metadata": {"title": "Different title", "abstract": "Full abstract", "authors_en": ["Alice"],
                               "funding_en": "Grant 123", "download_links": [{"format": "ris", "url": "https://example.com/a.ris"}]},
                  "audit": {"status": "conflict", "conflicts": ["title"]}}
        paper = db.save_metadata_audit(self.pid, result)
        self.assertEqual(paper["title"], "Stored title")
        self.assertEqual(paper["abstract"], "Full abstract")
        self.assertEqual(paper["authors_en"], ["Alice"])
        self.assertEqual(paper["notes"], "Keep me")
        self.assertTrue(paper["starred"])
        self.assertEqual(paper["source_metadata"]["funding_en"], "Grant 123")
        db.save_metadata_audit(self.pid, {"metadata": {}, "audit": {"status": "unavailable"}})
        self.assertEqual(db.get_paper(self.pid)["source_metadata"], result["metadata"])

    def test_sync_invalidates_changed_evidence_only(self):
        db.save_metadata_audit(self.pid, {"metadata": {}, "audit": {"status": "verified"}})
        db.upsert_paper(self.item | {"topics": ["risk"]})
        self.assertEqual(db.get_paper(self.pid)["metadata_audit"]["status"], "verified")
        db.upsert_paper(self.item | {"abstract": "New information"})
        self.assertEqual(db.get_paper(self.pid)["metadata_audit"]["status"], "stale")
        self.assertEqual(db.get_paper(self.pid)["metadata_audit"]["acceptance"]["status"], "pending")

    def test_export_ris_and_complete_json(self):
        paper = db.get_paper(self.pid)
        content, filename, _ = db.export_papers([paper], "ris")
        self.assertIn("TY  - JOUR", content)
        self.assertIn("AU  - Alice", content)
        self.assertTrue(filename.endswith(".ris"))
        content, _, _ = db.export_papers([paper], "json")
        self.assertEqual(json.loads(content)[0]["source_metadata"], {})

    def test_export_bilingual_csv_and_safe_bibtex_and_ris_pages(self):
        paper = db.get_paper(self.pid)
        paper.update({"title": r"Risk {A} \ data & 10%", "abstract": "English abstract", "pages": "12–25",
                      "source_metadata": {"title_zh": "风险研究", "title_en": "Risk research", "affiliations_en": "University",
                                          "funding_zh": "基金123", "keywords_en": ["risk", "data"], "pdf_url": "https://example.com/a.pdf"},
                      "metadata_audit": {"status": "partial", "checked_at": "2026-11-05"}})
        content, _, _ = db.export_papers([paper], "csv")
        row = next(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
        self.assertEqual(row["中文标题"], "风险研究")
        self.assertEqual(row["英文机构"], "University")
        self.assertEqual(row["中文基金"], "基金123")
        self.assertEqual(row["英文摘要"], "English abstract")
        self.assertEqual(row["英文关键词"], "risk; data")
        self.assertEqual(row["审计状态"], "partial")
        self.assertEqual(row["官网PDF链接"], "https://example.com/a.pdf")
        content, _, _ = db.export_papers([paper], "bibtex")
        self.assertIn(r"Risk \{A\} {\textbackslash{}} data \& 10\%", content)
        content, _, _ = db.export_papers([paper], "ris")
        self.assertIn("SP  - 12\nEP  - 25", content)

    def test_agent_exception_is_recorded(self):
        with patch("app.article_metadata.enrich_and_audit", side_effect=RuntimeError("offline")):
            paper = sync.audit_paper(self.pid)
        self.assertEqual(paper["metadata_audit"]["status"], "failed")
        self.assertEqual(paper["metadata_audit"]["errors"], ["offline"])

    def test_single_paper_export_does_not_include_other_records(self):
        db.upsert_paper(self.item | {"fingerprint": "other", "title": "Other paper"})
        handler = object.__new__(server.Handler)
        captured = []
        handler._send_bytes = lambda body, *args, **kwargs: captured.append(body)
        handler._api_get("/api/export", {"format": "json", "ids": str(self.pid)})
        self.assertEqual([p["id"] for p in json.loads(captured[0])], [self.pid])
        with self.assertRaises(server.ApiError):
            handler._api_get("/api/export", {"format": "json", "ids": "bad"})

    def test_audit_endpoint_returns_persisted_result(self):
        handler = object.__new__(server.Handler)
        captured = []
        handler._send_json = lambda result, *args: captured.append(result)
        result = {"metadata": {"abstract": "From original"}, "audit": {"status": "partial"}}
        with patch("app.article_metadata.enrich_and_audit", return_value=result):
            handler._api_post(f"/api/papers/{self.pid}/audit", {})
        self.assertEqual(captured[0]["paper"]["abstract"], "From original")
        self.assertEqual(db.get_paper(self.pid)["metadata_audit"]["status"], "partial")


if __name__ == "__main__":
    unittest.main()
