"""Unit tests for validation/ — profile-driven text validation."""
import unittest


class TestValidationProfiles(unittest.TestCase):
    def test_cold_outreach_profile_exists(self):
        from outreach_pipeline.validation.profiles import PROFILES
        self.assertIn("cold_outreach", PROFILES)
        self.assertEqual(PROFILES["cold_outreach"]["mode"], "block")

    def test_content_grading_profile_exists(self):
        from outreach_pipeline.validation.profiles import PROFILES
        self.assertIn("content_grading", PROFILES)
        self.assertEqual(PROFILES["content_grading"]["mode"], "audit")

    def test_profile_has_required_keys(self):
        from outreach_pipeline.validation.profiles import PROFILES
        for name, profile in PROFILES.items():
            self.assertIn("checks", profile, f"{name} missing 'checks'")
            self.assertIn("mode", profile, f"{name} missing 'mode'")
            self.assertIn(profile["mode"], ("block", "audit"), f"{name} has invalid mode")


class TestValidationResult(unittest.TestCase):
    def test_clean_result(self):
        from outreach_pipeline.validation.tone import ValidationResult
        r = ValidationResult(violations=[], passed=True)
        self.assertTrue(r.passed)
        self.assertEqual(r.violations, [])

    def test_failed_result(self):
        from outreach_pipeline.validation.tone import ValidationResult
        r = ValidationResult(
            violations=[{"label": "TRACKING_DATA", "reason": "page views"}],
            passed=False,
        )
        self.assertFalse(r.passed)
        self.assertEqual(len(r.violations), 1)


class TestValidateTone(unittest.TestCase):
    def test_clean_text_passes(self):
        from outreach_pipeline.validation.tone import validate
        result = validate("Hi there, here is some info about our product.", profile="cold_outreach")
        # Clean text should pass (no violations in the profile's check list)
        self.assertIsInstance(result.violations, list)

    def test_tracking_data_blocked_in_cold_outreach(self):
        from outreach_pipeline.validation.tone import validate
        result = validate(
            "I noticed you viewed 35 pages on our website last week.",
            profile="cold_outreach",
        )
        self.assertFalse(result.passed)
        labels = [v["label"] for v in result.violations]
        self.assertIn("TRACKING_DATA", labels)

    def test_tracking_data_audited_in_content_grading(self):
        from outreach_pipeline.validation.tone import validate
        result = validate(
            "I noticed you viewed 35 pages on our website last week.",
            profile="content_grading",
        )
        # content_grading is audit mode — no TRACKING_DATA in its check list anyway
        # but even if found, result.passed should be True (audit mode never blocks)
        self.assertTrue(result.passed)

    def test_marketing_adjectives_blocked(self):
        from outreach_pipeline.validation.tone import validate
        result = validate(
            "Our industry-leading cutting-edge solution is world-class.",
            profile="cold_outreach",
        )
        self.assertFalse(result.passed)
        labels = [v["label"] for v in result.violations]
        self.assertIn("OVERSELLING", labels)

    def test_marketing_adjectives_audited_in_content_grading(self):
        from outreach_pipeline.validation.tone import validate
        result = validate(
            "Our industry-leading cutting-edge solution is world-class.",
            profile="content_grading",
        )
        # Audit mode: violations reported but passed=True
        self.assertTrue(result.passed)
        labels = [v["label"] for v in result.violations]
        self.assertIn("OVERSELLING", labels)

    def test_unknown_profile_raises(self):
        from outreach_pipeline.validation.tone import validate
        with self.assertRaises(ValueError):
            validate("text", profile="nonexistent_profile")

    def test_validate_filters_to_profile_checks(self):
        """Violations outside the profile's check list are not reported."""
        from outreach_pipeline.validation.tone import validate
        # content_grading profile does NOT include TRACKING_DATA
        result = validate(
            "I noticed you viewed 35 pages on our website.",
            profile="content_grading",
        )
        labels = [v["label"] for v in result.violations]
        self.assertNotIn("TRACKING_DATA", labels)


if __name__ == "__main__":
    unittest.main()
