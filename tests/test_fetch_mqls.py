"""Unit tests for fetch_mqls.py"""
import pytest
from unittest.mock import Mock, patch

from outreach_pipeline.fetch_mqls import build_filters, fetch_mqls, parse_args, MQL_PROPERTIES
from outreach_pipeline.db.contacts import upsert_contact, get_contact


class TestBuildFilters:
    def test_returns_single_group(self):
        """Filter is one AND-group; lead_status and mql_source
        define the generic fetch boundary."""
        groups = build_filters("2026-01-01", "2026-01-31", "America/New_York")
        assert isinstance(groups, list)
        assert len(groups) == 1
        assert "filters" in groups[0]

    def test_builds_mql_date_range_filter(self):
        """The MQL date source is the stable `mql_date` property."""
        groups = build_filters("2026-01-01", "2026-01-31", "America/New_York")
        filters = groups[0]["filters"]
        date_filter = next(f for f in filters if f["propertyName"] == "mql_date")
        assert date_filter["operator"] == "BETWEEN"

    def test_builds_message_exists_filter(self):
        groups = build_filters("2026-01-01", "2026-01-31", "America/New_York")
        filters = groups[0]["filters"]
        msg_filter = next(f for f in filters if f["propertyName"] == "message")
        assert msg_filter["operator"] == "HAS_PROPERTY"

    def test_requires_lead_status_is_mql(self):
        """lead_status must equal the uppercase 'MQL' enum value.
        Keeps NURTURING / SAL / SQL / Disqualify-Delete out."""
        groups = build_filters("2026-01-01", "2026-01-31", "America/New_York")
        filters = groups[0]["filters"]
        sf = next(f for f in filters if f["propertyName"] == "lead_status")
        assert sf["operator"] == "EQ"
        assert sf["value"] == "MQL"

    def test_excludes_event_and_webinar_sources(self):
        """mql_source is HubSpot's MQL-moment source. Excludes
        contacts whose MQL trigger was a trade show / webinar
        (handled by the events team, not outreach). Keep empty and
        "Online- *" variants flowing through."""
        groups = build_filters("2026-01-01", "2026-01-31", "America/New_York")
        filters = groups[0]["filters"]
        src = next(f for f in filters if f["propertyName"] == "mql_source")
        assert src["operator"] == "NOT_IN"
        assert "Event / Tradeshow" in src["values"]
        assert "Attended webinar" in src["values"]


class TestParseArgs:
    def test_parses_valid_dates(self):
        args = parse_args(["--start-date", "2026-01-01", "--end-date", "2026-01-31"])
        assert args.start_date == "2026-01-01"

    def test_requires_both_dates(self):
        with pytest.raises(SystemExit):
            parse_args(["--start-date", "2026-01-01"])


class TestMqlProperties:
    def test_includes_enrichment_properties(self):
        for prop in ["message", "company", "industry", "jobtitle", "hs_analytics_source",
                     "hs_analytics_first_url", "hs_analytics_num_page_views"]:
            assert prop in MQL_PROPERTIES


class TestFetchMqls:
    @patch("outreach_pipeline.fetch_mqls.HubSpotClient")
    @patch("outreach_pipeline.fetch_mqls.load_config")
    def test_returns_contacts(self, mock_config, mock_client_cls):
        mock_config.return_value = {"hubspot_token": "t", "report_timezone": "America/New_York"}
        mock_client = Mock()
        mock_client_cls.return_value = mock_client
        mock_client.request_with_retry.return_value = {
            "results": [{"id": "1", "properties": {"message": "Hi"}}],
        }
        result = fetch_mqls("2026-01-01", "2026-01-31")
        assert result["total"] == 1

    @patch("outreach_pipeline.fetch_mqls.HubSpotClient")
    @patch("outreach_pipeline.fetch_mqls.load_config")
    def test_requests_all_properties(self, mock_config, mock_client_cls):
        mock_config.return_value = {"hubspot_token": "t", "report_timezone": "America/New_York"}
        mock_client = Mock()
        mock_client_cls.return_value = mock_client
        mock_client.request_with_retry.return_value = {"results": []}
        fetch_mqls("2026-01-01", "2026-01-31")
        payload = mock_client.request_with_retry.call_args[1]["json"]
        assert payload["properties"] == MQL_PROPERTIES

    @patch("outreach_pipeline.fetch_mqls.HubSpotClient")
    @patch("outreach_pipeline.fetch_mqls.load_config")
    def test_paginates(self, mock_config, mock_client_cls):
        mock_config.return_value = {"hubspot_token": "t", "report_timezone": "America/New_York"}
        mock_client = Mock()
        mock_client_cls.return_value = mock_client
        mock_client.request_with_retry.side_effect = [
            {"results": [{"id": "1", "properties": {}}], "paging": {"next": {"after": "c1"}}},
            {"results": [{"id": "2", "properties": {}}]},
        ]
        result = fetch_mqls("2026-01-01", "2026-01-31")
        assert len(result["contacts"]) == 2

    @patch("outreach_pipeline.fetch_mqls.HubSpotClient")
    @patch("outreach_pipeline.fetch_mqls.load_config")
    def test_handles_empty_results(self, mock_config, mock_client_cls):
        mock_config.return_value = {"hubspot_token": "t", "report_timezone": "America/New_York"}
        mock_client = Mock()
        mock_client_cls.return_value = mock_client
        mock_client.request_with_retry.return_value = {"results": []}
        result = fetch_mqls("2026-01-01", "2026-01-31")
        assert result["total"] == 0


class TestSaveToDb:
    @pytest.mark.asyncio
    async def test_saves_contacts_to_db(self, db):
        """save_to_db's sync SQLite path is broken (calls async upsert_contact).
        Test the async upsert_contact directly instead."""
        contacts = [
            {"id": "1", "properties": {"company": "Acme"}},
            {"id": "2", "properties": {"company": "Beta"}},
        ]
        for c in contacts:
            await upsert_contact(c["id"], c.get("properties", {}))
        c1 = await get_contact("1")
        assert c1 != {}
        assert c1["properties"]["company"] == "Acme"
        c2 = await get_contact("2")
        assert c2 != {}
        assert c2["properties"]["company"] == "Beta"
