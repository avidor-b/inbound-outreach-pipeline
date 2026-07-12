"""Unit tests for services/grading.py — content grading domain (separate from messaging)."""
import os
import tempfile
import unittest
from pathlib import Path


class TestGradeChunk(unittest.TestCase):
    def test_clean_chunk_scores_high(self):
        from outreach_pipeline.services.grading import grade_chunk
        result = grade_chunk("Our product helps with network planning and deployment.")
        self.assertGreaterEqual(result.quality_score, 80.0)

    def test_bad_chunk_has_violations(self):
        from outreach_pipeline.services.grading import grade_chunk
        result = grade_chunk(
            "Our industry-leading cutting-edge solution delivers world-class results."
        )
        self.assertGreater(len(result.violations), 0)
        labels = [v["label"] for v in result.violations]
        self.assertIn("OVERSELLING", labels)

    def test_grade_uses_content_grading_profile(self):
        from outreach_pipeline.services.grading import grade_chunk
        # Content grading profile should NOT check TRACKING_DATA
        result = grade_chunk("I noticed you viewed 35 pages on our website.")
        labels = [v["label"] for v in result.violations]
        self.assertNotIn("TRACKING_DATA", labels)


class TestBuildReport(unittest.TestCase):
    def test_empty_grades(self):
        from outreach_pipeline.services.grading import build_report
        from outreach_pipeline.types import GradingReport
        report = build_report([])
        self.assertEqual(report.total_chunks, 0)
        self.assertEqual(report.overall_score, 100.0)

    def test_aggregates_violations(self):
        from outreach_pipeline.services.grading import grade_chunk, build_report
        grades = [
            grade_chunk("Our industry-leading solution."),
            grade_chunk("Clean content about network deployment."),
            grade_chunk("Another cutting-edge world-class product."),
        ]
        report = build_report(grades)
        self.assertEqual(report.total_chunks, 3)
        self.assertIn("OVERSELLING", report.violations_by_type)
        self.assertLess(report.overall_score, 100.0)


class TestGradeCollection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_rag")
        cls.materials_dir = os.path.join(cls.tmpdir, "materials")
        os.makedirs(cls.materials_dir)
        Path(os.path.join(cls.materials_dir, "good.txt")).write_text(
            "Our product helps with network planning."
        )
        Path(os.path.join(cls.materials_dir, "bad.txt")).write_text(
            "Our industry-leading cutting-edge solution delivers world-class results."
        )
        from outreach_pipeline.index_materials import index_materials
        index_materials(cls.materials_dir, cls.db_path, collection_name="grade_test")

    def test_grade_collection(self):
        from outreach_pipeline.services.grading import grade_collection
        report = grade_collection("grade_test", db_path=self.db_path)
        self.assertGreater(report.total_chunks, 0)


if __name__ == "__main__":
    unittest.main()
