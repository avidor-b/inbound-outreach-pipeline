"""Unit tests for tools/grading.py — content grading domain tools."""
import os
import tempfile
import unittest
from pathlib import Path


class TestGradeCollectionTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_rag")
        cls.materials_dir = os.path.join(cls.tmpdir, "materials")
        os.makedirs(cls.materials_dir)
        Path(os.path.join(cls.materials_dir, "good.txt")).write_text(
            "Our product helps with network planning and deployment."
        )
        Path(os.path.join(cls.materials_dir, "bad.txt")).write_text(
            "Our industry-leading cutting-edge solution delivers world-class results."
        )
        from outreach_pipeline.index_materials import index_materials
        index_materials(cls.materials_dir, cls.db_path, collection_name="grade_tool_test")

    def test_grade_collection_returns_dict(self):
        from outreach_pipeline.tools.grading import grade_collection
        result = grade_collection(collection="grade_tool_test", db_path=self.db_path)
        self.assertIn("total_chunks", result)
        self.assertGreater(result["total_chunks"], 0)

    def test_grade_collection_finds_violations(self):
        from outreach_pipeline.tools.grading import grade_collection
        result = grade_collection(collection="grade_tool_test", db_path=self.db_path)
        self.assertIn("violations_by_type", result)
        self.assertIn("OVERSELLING", result["violations_by_type"])

    def test_grade_collection_has_overall_score(self):
        from outreach_pipeline.tools.grading import grade_collection
        result = grade_collection(collection="grade_tool_test", db_path=self.db_path)
        self.assertIn("overall_score", result)
        self.assertLess(result["overall_score"], 100.0)

    def test_grade_nonexistent_collection(self):
        from outreach_pipeline.tools.grading import grade_collection
        result = grade_collection(collection="no_such_col", db_path=self.db_path)
        self.assertEqual(result["total_chunks"], 0)


if __name__ == "__main__":
    unittest.main()
