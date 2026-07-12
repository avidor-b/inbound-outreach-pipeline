"""Unit tests for adapters — thin wrappers around existing modules."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
from outreach_pipeline.adapters.smtp import SMTPAdapter
from outreach_pipeline.adapters.excel import ExcelAdapter
from outreach_pipeline.types import RAGResult


class TestChromaDBAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_rag")
        cls.materials_dir = os.path.join(cls.tmpdir, "materials")
        os.makedirs(cls.materials_dir)
        Path(os.path.join(cls.materials_dir, "doc1.txt")).write_text(
            "Our product helps with network optimization and backhaul planning."
        )
        Path(os.path.join(cls.materials_dir, "doc2.txt")).write_text(
            "Case study: Customer deployed 500 units across 3 countries."
        )
        cls.adapter = ChromaDBAdapter(db_path=cls.db_path)
        cls.adapter.index(cls.materials_dir, collection_name="test_col")

    def test_index_returns_stats(self):
        result = self.adapter.index(self.materials_dir, collection_name="test_col2")
        self.assertTrue(result["success"])
        self.assertEqual(result["files_indexed"], 2)
        self.assertGreater(result["chunks_indexed"], 0)

    def test_query_returns_rag_results(self):
        results = self.adapter.query("network optimization", top_k=2, collection_name="test_col")
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        self.assertIsInstance(results[0], RAGResult)
        self.assertEqual(results[0].collection, "test_col")

    def test_query_nonexistent_collection(self):
        results = self.adapter.query("test", collection_name="nonexistent_xyz")
        self.assertEqual(results, [])

    def test_get_all_chunks(self):
        chunks = self.adapter.get_all_chunks(collection_name="test_col")
        self.assertIsInstance(chunks, list)
        self.assertGreater(len(chunks), 0)
        self.assertIsInstance(chunks[0], RAGResult)

    def test_get_all_chunks_nonexistent(self):
        chunks = self.adapter.get_all_chunks(collection_name="no_such_col")
        self.assertEqual(chunks, [])


class TestSMTPAdapter(unittest.TestCase):
    def test_check_config_disabled(self):
        adapter = SMTPAdapter(config={"email_sending_enabled": False})
        ok, msg = adapter.check_config()
        self.assertFalse(ok)
        self.assertIn("disabled", msg)

    def test_check_config_missing_fields(self):
        adapter = SMTPAdapter(config={"email_sending_enabled": True})
        ok, msg = adapter.check_config()
        self.assertFalse(ok)
        self.assertIn("Missing", msg)

    def test_check_config_complete(self):
        adapter = SMTPAdapter(config={
            "email_sending_enabled": True,
            "sending_email": "test@example.com",
            "smtp_token_id": "id",
            "smtp_token_password": "pw",
        })
        ok, msg = adapter.check_config()
        self.assertTrue(ok)


class TestExcelAdapter(unittest.TestCase):
    @patch("outreach_pipeline.export_pipeline.export_pipeline", new_callable=AsyncMock)
    def test_export_delegates(self, mock_export):
        mock_export.return_value = "/tmp/out.xlsx"
        adapter = ExcelAdapter()
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            adapter.export(output_path="/tmp/out.xlsx")
        )
        mock_export.assert_called_once_with("/tmp/out.xlsx")
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
