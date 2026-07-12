"""Unit tests for sanitize_output.py hook."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".claude", "hooks"))
from sanitize_output import sanitize, audit_log, AUDIT_LOG


class TestSanitize(unittest.TestCase):
    def test_redacts_dollar_amounts(self):
        text, r = sanitize("The deal is worth $150,000 annually")
        self.assertNotIn("$150,000", text)
        self.assertIn("[REDACTED:CURRENCY]", text)
        self.assertEqual(len(r), 1)

    def test_redacts_euro_amounts(self):
        text, _ = sanitize("Contract value €2.5M")
        self.assertNotIn("€2.5M", text)

    def test_redacts_arr_mrr(self):
        text, _ = sanitize("They are at 500K ARR")
        self.assertNotIn("500K ARR", text)

    def test_redacts_deal_terms(self):
        text, r = sanitize("Signed a 3-year contract with 60% margin")
        self.assertNotIn("3-year contract", text)
        self.assertNotIn("60% margin", text)
        self.assertTrue(any(x["label"] == "DEAL_TERMS" for x in r))

    def test_redacts_email_addresses(self):
        text, _ = sanitize("Contact jane@example.com for details")
        self.assertNotIn("jane@example.com", text)
        self.assertIn("[REDACTED:PII_EMAIL]", text)

    def test_redacts_phone_numbers(self):
        text, _ = sanitize("Call (555) 123-4567")
        self.assertNotIn("(555) 123-4567", text)

    def test_redacts_ssn(self):
        text, _ = sanitize("SSN: 123-45-6789")
        self.assertNotIn("123-45-6789", text)

    def test_redacts_confidential_keywords(self):
        text, r = sanitize("This document is CONFIDENTIAL and under NDA")
        self.assertIn("[REDACTED:CONFIDENTIAL]", text)
        self.assertTrue(any(x["label"] == "CONFIDENTIAL" for x in r))

    def test_passes_clean_text(self):
        text, r = sanitize("This is a normal product description with no sensitive data")
        self.assertEqual(r, [])
        self.assertNotIn("REDACTED", text)

    def test_multiple_redactions(self):
        text, r = sanitize("Deal: $500K, contact jane@example.com, confidential")
        self.assertTrue(len(r) >= 3)

    def test_preserves_surrounding_text(self):
        text, _ = sanitize("Before $100 after")
        self.assertIn("Before", text)
        self.assertIn("after", text)


class TestAuditLog(unittest.TestCase):
    def test_writes_audit_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            with patch("sanitize_output.AUDIT_LOG", type(AUDIT_LOG)(log_path)):
                audit_log("Bash", [{"label": "CURRENCY", "value": "$150,000"}])
                content = open(log_path).read()
                entry = json.loads(content.strip())
                self.assertEqual(entry["tool"], "Bash")
                self.assertEqual(len(entry["redactions"]), 1)

    def test_no_log_when_no_redactions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.log")
            with patch("sanitize_output.AUDIT_LOG", type(AUDIT_LOG)(log_path)):
                audit_log("Bash", [])
                self.assertFalse(os.path.exists(log_path))


if __name__ == "__main__":
    unittest.main()
