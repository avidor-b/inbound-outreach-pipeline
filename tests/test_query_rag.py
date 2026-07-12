"""Unit tests for query_rag.py"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from outreach_pipeline.index_materials import index_materials
from outreach_pipeline.query_rag import query_materials, parse_args


class TestQueryMaterials(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Create a temp index with sample materials."""
        cls.tmpdir = tempfile.mkdtemp()
        mat_dir = Path(cls.tmpdir) / "materials"
        mat_dir.mkdir()
        (mat_dir / "product.txt").write_text(
            "Our platform automates data pipelines for enterprise customers. "
            "Key features include real-time monitoring, HIPAA compliance, and 99.9% uptime SLA."
        )
        (mat_dir / "case_study.txt").write_text(
            "Case Study: Acme Manufacturing reduced pipeline latency by 40% "
            "after switching to our platform. Their factory floor monitoring "
            "now runs in real-time instead of 15-minute batches."
        )
        (mat_dir / "pricing.txt").write_text(
            "We offer three tiers: Starter for small teams, Professional for "
            "mid-size companies, and Enterprise for large organizations with "
            "custom SLA and dedicated support."
        )
        cls.db_path = os.path.join(cls.tmpdir, "db")
        index_materials(str(mat_dir), cls.db_path)

    def test_returns_relevant_results(self):
        result = query_materials("manufacturing automation", top_k=2, db_path=self.db_path)
        self.assertTrue(result["success"])
        self.assertTrue(len(result["results"]) > 0)
        # The case study about manufacturing should be most relevant
        sources = [r["source"] for r in result["results"]]
        self.assertIn("case_study.txt", sources)

    def test_respects_top_k(self):
        result = query_materials("platform", top_k=1, db_path=self.db_path)
        self.assertEqual(len(result["results"]), 1)

    def test_returns_source_and_text(self):
        result = query_materials("pricing tiers", top_k=1, db_path=self.db_path)
        r = result["results"][0]
        self.assertIn("source", r)
        self.assertIn("text", r)
        self.assertTrue(len(r["text"]) > 0)

    def test_handles_missing_db(self):
        result = query_materials("test", db_path="/nonexistent/db")
        self.assertFalse(result["success"])


class TestParseArgs(unittest.TestCase):
    def test_parses_query(self):
        args = parse_args(["--query", "test query"])
        self.assertEqual(args.query, "test query")
        self.assertIsNone(args.top_k)  # defaults to None, resolved from config at runtime

    def test_parses_collection(self):
        args = parse_args(["--query", "test", "--collection", "customer_example_com"])
        self.assertEqual(args.collection, "customer_example_com")


class TestQueryCustomCollection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        mat_dir = Path(cls.tmpdir) / "materials"
        mat_dir.mkdir()
        (mat_dir / "doc.txt").write_text("Acme Corp builds industrial robots for automotive manufacturing.")
        cls.db_path = os.path.join(cls.tmpdir, "db")
        index_materials(str(mat_dir), cls.db_path, collection_name="customer_example_com")

    def test_queries_custom_collection(self):
        result = query_materials("robots manufacturing", top_k=1, db_path=self.db_path, collection_name="customer_example_com")
        self.assertTrue(result["success"])
        self.assertTrue(len(result["results"]) > 0)
        self.assertIn("Acme", result["results"][0]["text"])

    def test_wrong_collection_returns_error(self):
        result = query_materials("test", db_path=self.db_path, collection_name="nonexistent")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])


if __name__ == "__main__":
    unittest.main()
