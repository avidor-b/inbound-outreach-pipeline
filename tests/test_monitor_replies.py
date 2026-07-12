"""Unit tests for monitor_replies.py

filter_replies and parse_args are pure sync functions.
fetch_replies is sync but calls into db functions that are now async --
it's tested with mocks forced to MagicMock (not AsyncMock) to match
the sync calling convention fetch_replies expects.
"""
from unittest.mock import Mock, MagicMock, patch

from outreach_pipeline.monitor_replies import filter_replies, parse_args


class TestFilterReplies:
    def test_filters_incoming_after_date(self):
        emails = [
            {"direction": "INCOMING", "timestamp": "2026-02-18T10:00:00Z", "subject": "Re: Hi"},
            {"direction": "OUTGOING", "timestamp": "2026-02-18T09:00:00Z", "subject": "Hi"},
            {"direction": "INCOMING", "timestamp": "2026-02-10T10:00:00Z", "subject": "Old"},
        ]
        replies = filter_replies(emails, "2026-02-15")
        assert len(replies) == 1
        assert replies[0]["subject"] == "Re: Hi"

    def test_returns_empty_for_no_replies(self):
        emails = [{"direction": "OUTGOING", "timestamp": "2026-02-18T10:00:00Z"}]
        assert filter_replies(emails, "2026-02-15") == []

    def test_handles_missing_timestamp(self):
        emails = [{"direction": "INCOMING", "timestamp": ""}]
        assert filter_replies(emails, "2026-02-15") == []

    def test_handles_bad_timestamp(self):
        emails = [{"direction": "INCOMING", "timestamp": "not-a-date"}]
        assert filter_replies(emails, "2026-02-15") == []


class TestParseArgs:
    def test_parses_required_args(self):
        args = parse_args(["--since", "2026-02-16"])
        assert args.since == "2026-02-16"


class TestFetchReplies:
    @patch("outreach_pipeline.monitor_replies.HubSpotClient")
    @patch("outreach_pipeline.monitor_replies.load_config")
    @patch("outreach_pipeline.db.get_db")
    def test_fetches_and_saves_replies(self, mock_get_db, mock_config, mock_client_cls):
        mock_config.return_value = {"hubspot_token": "t", "report_timezone": "UTC"}
        mock_client = Mock()
        mock_client_cls.return_value = mock_client
        mock_client.get_contact_emails.return_value = [
            {"direction": "INCOMING", "timestamp": "2026-02-18T10:00:00Z", "subject": "Re: Hi", "body_preview": "Yes"},
        ]
        mock_conn = Mock()
        mock_get_db.return_value = mock_conn
        # Force MagicMock (not AsyncMock) so sync fetch_replies can call them
        with patch("outreach_pipeline.db.get_drafts", new_callable=MagicMock, return_value=[{"contact_id": "1", "status": "sent"}]):
            with patch("outreach_pipeline.db.save_reply", new_callable=MagicMock) as mock_save:
                from outreach_pipeline.monitor_replies import fetch_replies
                result = fetch_replies("2026-02-15")
                assert result["total"] == 1
                mock_save.assert_called_once()

    @patch("outreach_pipeline.monitor_replies.HubSpotClient")
    @patch("outreach_pipeline.monitor_replies.load_config")
    @patch("outreach_pipeline.db.get_db")
    def test_skips_contacts_with_no_replies(self, mock_get_db, mock_config, mock_client_cls):
        mock_config.return_value = {"hubspot_token": "t", "report_timezone": "UTC"}
        mock_client = Mock()
        mock_client_cls.return_value = mock_client
        mock_client.get_contact_emails.return_value = [
            {"direction": "OUTGOING", "timestamp": "2026-02-18T10:00:00Z"},
        ]
        mock_conn = Mock()
        mock_get_db.return_value = mock_conn
        with patch("outreach_pipeline.db.get_drafts", new_callable=MagicMock, return_value=[{"contact_id": "1", "status": "sent"}]):
            from outreach_pipeline.monitor_replies import fetch_replies
            result = fetch_replies("2026-02-15")
            assert result["total"] == 0
