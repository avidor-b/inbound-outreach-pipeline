"""Unit tests for draft_emails.py"""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from outreach_pipeline.draft_emails import save_draft, parse_args


def _mock_db():
    """Return a mock DB connection so tests never touch data/pipeline.db."""
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn


class TestSaveDraft(unittest.TestCase):
    def setUp(self):
        self.db_patch = patch("outreach_pipeline.db.get_db", return_value=_mock_db())
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()

    def test_creates_draft_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_draft("123", "Hello", "Body text", score=85, output_dir=tmpdir)
            self.assertTrue(os.path.exists(path))

    def test_draft_contains_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_draft("456", "Subject", "Body", score=70, output_dir=tmpdir)
            content = open(path, encoding="utf-8").read()
            self.assertIn('contact_id: "456"', content)
            self.assertIn('subject: "Subject"', content)
            self.assertIn("score: 70", content)
            self.assertIn("status: approved", content)

    def test_draft_contains_body(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_draft("789", "Sub", "Email body here", output_dir=tmpdir)
            content = open(path, encoding="utf-8").read()
            self.assertIn("Email body here", content)

    def test_creates_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "nested", "drafts")
            save_draft("1", "S", "B", output_dir=subdir)
            self.assertTrue(os.path.isdir(subdir))

    def test_filename_uses_contact_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_draft("abc123", "S", "B", output_dir=tmpdir)
            self.assertTrue(path.endswith("abc123.md"))


class TestParseArgs(unittest.TestCase):
    def test_parses_required_args(self):
        args = parse_args(["--contact-id", "1", "--subject", "Hi", "--body", "Hello"])
        self.assertEqual(args.contact_id, "1")
        self.assertEqual(args.subject, "Hi")
        self.assertEqual(args.body, "Hello")

    def test_default_output_dir(self):
        args = parse_args(["--contact-id", "1", "--subject", "S", "--body", "B"])
        self.assertEqual(args.output_dir, "data/drafts")


if __name__ == "__main__":
    unittest.main()
