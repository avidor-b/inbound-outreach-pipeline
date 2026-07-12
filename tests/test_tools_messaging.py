"""Unit tests for tools/messaging.py — messaging domain tools."""
import unittest

from outreach_pipeline.validation.tone import ValidationResult


class TestValidateMessage(unittest.TestCase):
    def test_validate_clean_text(self):
        from outreach_pipeline.tools.messaging import validate_message
        result = validate_message(
            "Hi [First Name], here is some info. Best, [Sender Name]",
            profile="cold_outreach",
        )
        self.assertIsInstance(result, ValidationResult)

    def test_validate_bad_text_blocked(self):
        from outreach_pipeline.tools.messaging import validate_message
        result = validate_message(
            "I noticed you viewed 35 pages on our website.",
            profile="cold_outreach",
        )
        self.assertFalse(result.passed)

    def test_validate_with_different_profiles(self):
        from outreach_pipeline.tools.messaging import validate_message
        # Same text, different profiles
        text = "Our industry-leading cutting-edge solution."
        cold = validate_message(text, profile="cold_outreach")
        grading = validate_message(text, profile="content_grading")
        # cold_outreach blocks OVERSELLING, content_grading audits it
        self.assertFalse(cold.passed)
        self.assertTrue(grading.passed)


if __name__ == "__main__":
    unittest.main()
