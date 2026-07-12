"""Unit tests for services/monitoring.py — reply monitoring."""
import unittest
from unittest.mock import patch


class TestFetchReplies(unittest.TestCase):
    @patch("outreach_pipeline.monitor_replies.fetch_replies")
    def test_fetch_replies_returns_dict(self, mock_fetch):
        mock_fetch.return_value = []
        from outreach_pipeline.services.monitoring import fetch_replies
        result = fetch_replies("2026-03-01")
        self.assertIn("replies", result)
        self.assertEqual(result["since"], "2026-03-01")

    @patch("outreach_pipeline.monitor_replies.fetch_replies")
    def test_fetch_replies_with_results(self, mock_fetch):
        mock_fetch.return_value = [
            {"contact_id": "1", "subject": "Re: Hi", "direction": "INCOMING"},
        ]
        from outreach_pipeline.services.monitoring import fetch_replies
        result = fetch_replies("2026-03-01")
        self.assertEqual(len(result["replies"]), 1)


if __name__ == "__main__":
    unittest.main()
