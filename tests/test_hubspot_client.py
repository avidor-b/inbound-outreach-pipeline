"""Unit tests for hubspot_client.py — get_contact_emails."""
import unittest
from unittest.mock import patch, Mock, call

from outreach_pipeline.hubspot_client import HubSpotClient


def _make_client():
    return HubSpotClient("fake-token", {"report_timezone": "America/New_York"})


class TestGetContactEmails(unittest.TestCase):
    @patch.object(HubSpotClient, "request_with_retry")
    def test_returns_emails_with_correct_fields(self, mock_req):
        mock_req.side_effect = [
            {"results": [{"id": "e1"}]},
            {"properties": {
                "hs_email_subject": "Re: Hello",
                "hs_email_text": "Thanks for reaching out",
                "hs_email_direction": "INCOMING",
                "hs_timestamp": "2026-02-18T10:00:00Z",
            }},
        ]
        emails = _make_client().get_contact_emails("123")
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["subject"], "Re: Hello")
        self.assertEqual(emails[0]["direction"], "INCOMING")
        self.assertEqual(emails[0]["body_preview"], "Thanks for reaching out")

    @patch.object(HubSpotClient, "request_with_retry")
    def test_returns_empty_list_when_no_associations(self, mock_req):
        mock_req.return_value = {"results": []}
        self.assertEqual(_make_client().get_contact_emails("999"), [])
        mock_req.assert_called_once()

    @patch.object(HubSpotClient, "request_with_retry")
    def test_fetches_each_email_individually(self, mock_req):
        mock_req.side_effect = [
            {"results": [{"id": "e1"}, {"id": "e2"}]},
            {"properties": {"hs_email_subject": "A", "hs_email_text": "", "hs_email_direction": "OUTGOING", "hs_timestamp": ""}},
            {"properties": {"hs_email_subject": "B", "hs_email_text": "", "hs_email_direction": "INCOMING", "hs_timestamp": ""}},
        ]
        emails = _make_client().get_contact_emails("456")
        self.assertEqual(len(emails), 2)
        self.assertEqual(mock_req.call_count, 3)

    @patch.object(HubSpotClient, "request_with_retry")
    def test_handles_missing_properties(self, mock_req):
        mock_req.side_effect = [
            {"results": [{"id": "e1"}]},
            {"properties": {}},
        ]
        emails = _make_client().get_contact_emails("789")
        self.assertEqual(emails[0]["subject"], "")
        self.assertEqual(emails[0]["direction"], "")


if __name__ == "__main__":
    unittest.main()
