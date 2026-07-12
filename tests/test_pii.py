"""Tests for PII handling -- stripping, redaction, on-demand fetch."""
import pytest

from outreach_pipeline.db.contacts import (
    upsert_contact, get_contact, _strip_pii, _redact_message_pii, PII_FIELDS,
)


class TestPIIFields:
    def test_pii_fields_include_jobtitle(self):
        assert "jobtitle" in PII_FIELDS

    def test_pii_fields_include_standard(self):
        for field in ("firstname", "lastname", "email", "phone", "mobilephone"):
            assert field in PII_FIELDS


class TestStripPII:
    def test_strips_all_pii_fields(self):
        props = {
            "firstname": "John", "lastname": "Doe", "email": "john@example.com",
            "phone": "+1-555-1234", "jobtitle": "VP Engineering",
            "company": "Acme", "message": "Need pricing",
        }
        stripped = _strip_pii(props)
        for field in PII_FIELDS:
            assert field not in stripped
        assert stripped["company"] == "Acme"
        assert "message" in stripped

    def test_preserves_email_domain(self):
        stripped = _strip_pii({"email": "user@customer.example.com"})
        assert stripped["email_domain"] == "customer.example.com"

    def test_freemail_sets_has_email(self):
        stripped = _strip_pii({"email": "user@example.com"})
        assert "email_domain" not in stripped
        assert stripped["has_email"] is True

    def test_redacts_pii_in_message(self):
        stripped = _strip_pii({
            "message": "My name is John Smith, call me at +1-555-1234 or john@example.com",
        })
        msg = stripped["message"]
        assert "John Smith" not in msg
        assert "+1-555-1234" not in msg
        assert "john@example.com" not in msg
        assert "[name redacted]" in msg
        assert "[phone redacted]" in msg
        assert "[email redacted]" in msg


class TestRedactMessagePII:
    def test_redacts_email(self):
        result = _redact_message_pii("Contact me at user@example.com for details")
        assert "user@example.com" not in result
        assert "[email redacted]" in result

    def test_redacts_phone(self):
        result = _redact_message_pii("Call +1-555-123-4567 for info")
        assert "+1-555-123-4567" not in result
        assert "[phone redacted]" in result

    def test_redacts_name_introduction(self):
        result = _redact_message_pii("My name is John Smith and I need pricing")
        assert "John Smith" not in result
        assert "[name redacted]" in result

    def test_redacts_i_am_introduction(self):
        result = _redact_message_pii("I am Jane Doe from Acme Corp")
        assert "Jane Doe" not in result

    def test_preserves_company_names(self):
        result = _redact_message_pii("We at Example Company need pricing for RADIO-A")
        assert "Example Company" in result
        assert "RADIO-A" in result

    def test_preserves_product_references(self):
        result = _redact_message_pii("Need info about mesh radio and RADIO-C8010")
        assert "mesh radio" in result
        assert "RADIO-C8010" in result

    def test_empty_message(self):
        result = _redact_message_pii("")
        assert result == ""

    def test_no_pii_unchanged(self):
        msg = "Need pricing for 10 units of RADIO-A for our network project"
        result = _redact_message_pii(msg)
        assert result == msg

    def test_multiple_pii_in_one_message(self):
        result = _redact_message_pii(
            "My name is Bob Jones, email bob@example.com, phone 555-123-4567"
        )
        assert "Bob Jones" not in result
        assert "bob@example.com" not in result
        assert "555-123-4567" not in result


class TestPIIInDatabase:
    @pytest.mark.asyncio
    async def test_jobtitle_stripped_on_store(self, db):
        await upsert_contact("1", {
            "company": "Acme", "jobtitle": "VP Engineering",
        })
        c = await get_contact("1")
        assert "jobtitle" not in c["properties"]

    @pytest.mark.asyncio
    async def test_message_pii_redacted_on_store(self, db):
        await upsert_contact("1", {
            "message": "My name is Alice Johnson, email alice@example.com",
        })
        c = await get_contact("1")
        assert "Alice Johnson" not in c["properties"]["message"]
        assert "alice@example.com" not in c["properties"]["message"]

    @pytest.mark.asyncio
    async def test_company_preserved(self, db):
        await upsert_contact("1", {
            "company": "Acme Corp", "jobtitle": "CTO",
            "firstname": "John", "email": "john@customer.example.com",
        })
        c = await get_contact("1")
        assert c["properties"]["company"] == "Acme Corp"
        assert c["properties"]["email_domain"] == "customer.example.com"


class TestPIITools:
    def test_fetch_contact_pii_module_exists(self):
        from outreach_pipeline.tools.pii import fetch_contact_pii, fetch_jobtitle, fetch_send_fields
        # These should be importable
        assert callable(fetch_contact_pii)
        assert callable(fetch_jobtitle)
        assert callable(fetch_send_fields)
