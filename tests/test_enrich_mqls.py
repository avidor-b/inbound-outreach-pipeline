"""Unit tests for enrich_mqls.py"""
import unittest
from unittest.mock import patch, Mock

from outreach_pipeline.enrich_mqls import parse_args


class TestParseArgs(unittest.TestCase):
    def test_parses_args(self):
        args = parse_args(["--contact-id", "123", "--company-context", "Makes robots"])
        self.assertEqual(args.contact_id, "123")
        self.assertEqual(args.company_context, "Makes robots")

    def test_defaults(self):
        args = parse_args(["--contact-id", "123"])
        self.assertEqual(args.company_context, "")
        self.assertEqual(args.crm_context, "")


class TestEnrichContact(unittest.TestCase):
    @patch("outreach_pipeline.db.enrich_contact")
    @patch("outreach_pipeline.db.get_db")
    def test_calls_db_enrich(self, mock_db, mock_enrich):
        mock_conn = Mock()
        mock_db.return_value = mock_conn
        from outreach_pipeline.enrich_mqls import main
        with patch("sys.argv", ["enrich-mqls", "--contact-id", "123", "--company-context", "Robots"]):
            main()
        mock_enrich.assert_called_once_with(mock_conn, "123", "Robots", "")


if __name__ == "__main__":
    unittest.main()
