"""Edge case tests for RAG indexing, querying, and ChromaDB adapter."""
import os
import tempfile
import unittest
from pathlib import Path

from outreach_pipeline.index_materials import (
    index_materials, _detect_domain, _detect_document_type,
)
from outreach_pipeline.query_rag import query_materials


class TestDomainDetection(unittest.TestCase):
    """_detect_domain should recognize underscore-formatted domain directories."""

    def test_customer_example_com(self):
        self.assertEqual(_detect_domain(Path("data/customer_example_com/page.txt")), "customer.example.com")

    def test_example_org(self):
        self.assertEqual(_detect_domain(Path("prospects/example_org/about.html")), "example.org")

    def test_site_co_uk(self):
        self.assertEqual(_detect_domain(Path("data/site_co_uk/index.txt")), "site.co.uk")

    def test_company_io(self):
        self.assertEqual(_detect_domain(Path("company_io/product.md")), "company.io")

    def test_no_domain_plain_dir(self):
        self.assertEqual(_detect_domain(Path("resources/materials/product.txt")), "")

    def test_no_domain_file_with_underscore(self):
        """Files with underscores shouldn't be detected as domains."""
        self.assertEqual(_detect_domain(Path("my_file_name.txt")), "")

    def test_no_domain_dotfile(self):
        self.assertEqual(_detect_domain(Path(".hidden/file.txt")), "")


class TestDocumentTypeDetection(unittest.TestCase):
    def test_product_path(self):
        self.assertEqual(_detect_document_type(Path("products/microwave_radio.txt")), "product")

    def test_blog_path(self):
        self.assertEqual(_detect_document_type(Path("blog/post.md")), "blog")

    def test_case_study_path(self):
        self.assertEqual(_detect_document_type(Path("case-studies/acme.txt")), "case_study")

    def test_generic_path(self):
        self.assertEqual(_detect_document_type(Path("misc/readme.txt")), "general")

    def test_csv_is_data(self):
        self.assertEqual(_detect_document_type(Path("exports/contacts.csv")), "data")

    def test_landing_page(self):
        self.assertEqual(_detect_document_type(Path("landing-pages/promo.html")), "landing_page")


class TestEmptyQueryString(unittest.TestCase):
    def test_empty_query_returns_results_or_empty(self):
        """Empty query should not crash — returns whatever ChromaDB matches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            (mat_dir / "doc.txt").write_text("Some content about wireless backhaul products.")
            db_path = os.path.join(tmpdir, "db")
            index_materials(str(mat_dir), db_path)

            result = query_materials("", top_k=3, db_path=db_path)
            self.assertTrue(result["success"])
            # Empty query might return results (ChromaDB behavior) or empty — just shouldn't crash


class TestMetadataFiltering(unittest.TestCase):
    def test_filter_nonexistent_type_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            (mat_dir / "doc.txt").write_text("Product information about RADIO-A wireless backhaul.")
            db_path = os.path.join(tmpdir, "db")
            index_materials(str(mat_dir), db_path)

            result = query_materials("RADIO-A", db_path=db_path, doc_type="nonexistent_type")
            self.assertTrue(result["success"])
            self.assertEqual(result["total"], 0)

    def test_filter_by_document_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            (mat_dir / "products").mkdir(parents=True)
            (mat_dir / "blog").mkdir(parents=True)
            (mat_dir / "products" / "microwave_radio.txt").write_text("RADIO-A is a wireless transport platform.")
            (mat_dir / "blog" / "news.txt").write_text("RADIO-A wins innovation award for wireless transport.")
            db_path = os.path.join(tmpdir, "db")
            index_materials(str(mat_dir), db_path)

            # Query only product docs
            result = query_materials("RADIO-A", db_path=db_path, doc_type="product")
            self.assertTrue(result["success"])
            for r in result["results"]:
                self.assertEqual(r["document_type"], "product")


class TestUpsertReplacement(unittest.TestCase):
    def test_reindex_replaces_old_content(self):
        """Re-indexing with changed content should update, not duplicate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            db_path = os.path.join(tmpdir, "db")

            # First index
            (mat_dir / "doc.txt").write_text("Original content about wireless backhaul.")
            r1 = index_materials(str(mat_dir), db_path)
            count1 = r1["collection_count"]

            # Re-index with different content, same filename
            (mat_dir / "doc.txt").write_text("Updated content about 5G transport.")
            r2 = index_materials(str(mat_dir), db_path)
            count2 = r2["collection_count"]

            # Should have same chunk count (upsert), not double
            self.assertEqual(count1, count2)

            # Verify content is updated
            result = query_materials("5G transport", db_path=db_path)
            self.assertTrue(any("5G" in r["text"] for r in result["results"]))

    def test_reset_then_reindex(self):
        """Reset should clear all chunks before re-indexing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            db_path = os.path.join(tmpdir, "db")

            (mat_dir / "a.txt").write_text("Content A about apples.")
            (mat_dir / "b.txt").write_text("Content B about bananas.")
            index_materials(str(mat_dir), db_path)

            # Remove file B, re-index with reset
            (mat_dir / "b.txt").unlink()
            r = index_materials(str(mat_dir), db_path, reset=True)
            self.assertEqual(r["files_indexed"], 1)

            # B's content should be gone
            result = query_materials("bananas", db_path=db_path)
            self.assertFalse(any("bananas" in r["text"] for r in result["results"]))


class TestRichMetadata(unittest.TestCase):
    def test_indexed_metadata_fields(self):
        """Indexed chunks should have all metadata fields."""
        import chromadb
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials" / "products"
            mat_dir.mkdir(parents=True)
            (mat_dir / "microwave_radio.txt").write_text("RADIO-A wireless transport platform specifications.")
            db_path = os.path.join(tmpdir, "db")
            index_materials(str(Path(tmpdir) / "materials"), db_path)

            client = chromadb.PersistentClient(path=db_path)
            col = client.get_collection("sales_materials")
            data = col.get(include=["metadatas"])
            meta = data["metadatas"][0]

            self.assertIn("source", meta)
            self.assertIn("chunk_index", meta)
            self.assertIn("document_type", meta)
            self.assertIn("domain", meta)
            self.assertIn("indexed_at", meta)
            self.assertEqual(meta["document_type"], "product")


import pytest as _pytest
from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter


class TestAsyncAdapter:
    @_pytest.mark.asyncio
    async def test_async_query_propagates_results(self):
        """aquery should return same results as sync query."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            (mat_dir / "doc.txt").write_text("Wireless backhaul technology for 5G networks.")
            db_path = os.path.join(tmpdir, "db")

            adapter = ChromaDBAdapter(db_path=db_path)
            adapter.index(str(mat_dir))

            sync_results = adapter.query("5G backhaul")
            async_results = await adapter.aquery("5G backhaul")
            assert len(sync_results) == len(async_results)

    @_pytest.mark.asyncio
    async def test_async_error_returns_empty(self):
        """aquery on nonexistent db should return empty, not crash."""
        adapter = ChromaDBAdapter(db_path="/tmp/nonexistent_rag_db_test")
        results = await adapter.aquery("anything")
        assert results == []


if __name__ == "__main__":
    unittest.main()
