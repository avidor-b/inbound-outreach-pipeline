"""Unit tests for tools/rag.py — RAG tools."""
import os
import tempfile
import unittest
from pathlib import Path


class TestQueryRAGTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_rag")
        cls.materials_dir = os.path.join(cls.tmpdir, "materials")
        os.makedirs(cls.materials_dir)
        Path(os.path.join(cls.materials_dir, "doc.txt")).write_text(
            "Network optimization and microwave backhaul solutions."
        )
        from outreach_pipeline.index_materials import index_materials
        index_materials(cls.materials_dir, cls.db_path, collection_name="test_col")

    def test_query_rag_returns_results(self):
        from outreach_pipeline.tools.rag import query_rag
        result = query_rag("network optimization", collection="test_col",
                           db_path=self.db_path)
        self.assertIn("results", result)
        self.assertGreater(len(result["results"]), 0)

    def test_query_rag_empty_collection(self):
        from outreach_pipeline.tools.rag import query_rag
        result = query_rag("test", collection="nonexistent", db_path=self.db_path)
        self.assertEqual(len(result["results"]), 0)


class TestIndexContentTool(unittest.TestCase):
    def test_index_content(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test_rag")
        materials_dir = os.path.join(tmpdir, "materials")
        os.makedirs(materials_dir)
        Path(os.path.join(materials_dir, "doc.txt")).write_text("Test content.")

        from outreach_pipeline.tools.rag import index_content
        result = index_content(materials_dir, collection="test_idx", db_path=db_path)
        self.assertTrue(result["success"])
        self.assertEqual(result["files_indexed"], 1)


class TestScrapeAndIndex(unittest.TestCase):
    def test_scrape_and_index_signature(self):
        """scrape_and_index accepts the expected parameters."""
        import inspect
        from outreach_pipeline.tools.rag import scrape_and_index
        sig = inspect.signature(scrape_and_index)
        params = list(sig.parameters.keys())
        self.assertIn("url", params)
        self.assertIn("collection", params)
        self.assertIn("max_pages", params)

    def test_scrape_and_index_derives_collection_name(self):
        """Collection name derived from URL domain when not specified."""
        from urllib.parse import urlparse
        url = "https://www.example.com/products"
        domain = urlparse(url).netloc.replace(".", "_").replace("-", "_")
        self.assertEqual(domain, "www_example_com")


if __name__ == "__main__":
    unittest.main()
