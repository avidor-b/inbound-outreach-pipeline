"""Unit tests for send_email.py"""
import unittest
from unittest.mock import patch, MagicMock

from outreach_pipeline.send_email import check_config, send_email, parse_args

VALID_CONFIG = {
    "email_sending_enabled": True,
    "sending_email": "me@example.com",
    "sender_name": "Me",
    "smtp_token_id": "token-id",
    "smtp_token_password": "token-pass",
    "report_timezone": "UTC",
}


class TestCheckConfig(unittest.TestCase):
    def test_disabled_by_default(self):
        ok, msg = check_config({})
        self.assertFalse(ok)
        self.assertIn("disabled", msg)

    def test_missing_smtp_fields(self):
        ok, msg = check_config({"email_sending_enabled": True, "sending_email": "me@example.com"})
        self.assertFalse(ok)
        self.assertIn("smtp_token_id", msg)

    def test_valid_config(self):
        ok, msg = check_config(VALID_CONFIG)
        self.assertTrue(ok)


class TestSendEmail(unittest.TestCase):
    def _patch(self, config, to_email="contact@example.com"):
        patches = [
            patch("outreach_pipeline.send_email.load_config", return_value=config),
            patch("outreach_pipeline.send_email._get_contact_props", return_value=("John", to_email)),
        ]
        return patches

    def test_refuses_when_disabled(self):
        with patch("outreach_pipeline.send_email.load_config", return_value={}), \
             patch("outreach_pipeline.send_email._get_contact_props", return_value=("there", "x@example.com")):
            result = send_email("123", "Hi", "Body")
        self.assertFalse(result["success"])
        self.assertIn("disabled", result["error"])

    def test_fails_when_no_email_on_file(self):
        with patch("outreach_pipeline.send_email.load_config", return_value=VALID_CONFIG), \
             patch("outreach_pipeline.send_email._get_contact_props", return_value=("there", "")):
            result = send_email("123", "Hi", "Body")
        self.assertFalse(result["success"])
        self.assertIn("No email address", result["error"])

    def test_dry_run_does_not_send(self):
        for p in (patches := self._patch(VALID_CONFIG)):
            p.start()
        try:
            result = send_email("123", "Hi", "Body", dry_run=True)
        finally:
            for p in patches:
                p.stop()
        self.assertTrue(result["success"])
        self.assertFalse(result["sent"])
        self.assertTrue(result["dry_run"])

    def test_sends_via_smtp(self):
        mock_smtp = MagicMock()
        with patch("outreach_pipeline.send_email.load_config", return_value=VALID_CONFIG), \
             patch("outreach_pipeline.send_email._get_contact_props", return_value=("John", "contact@example.com")), \
             patch("outreach_pipeline.send_email.smtplib.SMTP", return_value=mock_smtp.__enter__.return_value):
            mock_smtp.__enter__.return_value.send_message = MagicMock()
            result = send_email("123", "Hi [First Name]", "Hello [First Name], from [Name]")
        self.assertTrue(result["success"])
        self.assertTrue(result["sent"])

    def test_placeholders_resolved(self):
        from outreach_pipeline.send_email import resolve_placeholders
        out = resolve_placeholders("Hi [First Name], from [Name]", "Sarah", "Kelly Smith")
        self.assertEqual(out, "Hi Sarah, from Kelly Smith")


class TestParseArgs(unittest.TestCase):
    def test_parses_args(self):
        args = parse_args(["--contact-id", "1", "--subject", "S", "--body", "B"])
        self.assertEqual(args.contact_id, "1")
        self.assertFalse(args.dry_run)

    def test_dry_run_flag(self):
        args = parse_args(["--contact-id", "1", "--subject", "S", "--body", "B", "--dry-run"])
        self.assertTrue(args.dry_run)


if __name__ == "__main__":
    unittest.main()
