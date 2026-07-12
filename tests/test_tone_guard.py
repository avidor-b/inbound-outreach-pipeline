"""Unit tests for tone_guard.py hook."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".claude", "hooks"))
from tone_guard import check_tone, extract_email_content

# Minimal valid email body — used by tests that need a clean baseline
_CLEAN_EMAIL = (
    "Hi [First Name],\n\n"
    "The split-mount radio RADIO-A fits your microwave backhaul requirements. "
    "Are you at the spec stage already, or still evaluating options?\n\n"
    "Best,\n[Sender Name]"
)


class TestCheckTone(unittest.TestCase):
    def test_clean_email_passes(self):
        v = check_tone(_CLEAN_EMAIL, body=_CLEAN_EMAIL)
        self.assertEqual(v, [])

    def test_catches_profanity(self):
        v = check_tone("This damn product is what you need")
        self.assertTrue(any(x["label"] == "PROFANITY" for x in v))

    def test_catches_aggressive_tone(self):
        v = check_tone("You must buy this now or you're making a mistake")
        self.assertTrue(any(x["label"] == "AGGRESSIVE" for x in v))

    def test_catches_manipulative_tactics(self):
        v = check_tone("Limited time offer! Act now! Your competitors are already using this")
        self.assertTrue(any(x["label"] == "MANIPULATIVE" for x in v))

    def test_catches_false_claims(self):
        v = check_tone("Our product is the best in the world with 100% guaranteed results")
        self.assertTrue(any(x["label"] == "FALSE_CLAIMS" for x in v))

    def test_catches_discriminatory_language(self):
        v = check_tone("Typical for your type of company")
        self.assertTrue(any(x["label"] == "DISCRIMINATORY" for x in v))

    def test_catches_excessive_caps(self):
        v = check_tone("This is ABSOLUTELY INCREDIBLE and TOTALLY AMAZING for your BUSINESS")
        self.assertTrue(any(x["label"] == "SHOUTING" for x in v))

    def test_allows_normal_acronyms(self):
        body = (
            "Hi [First Name],\n\n"
            "The platform integrates via REST API and carries SOC2 and HIPAA certifications. "
            "Are you evaluating this for a specific deployment?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertEqual(v, [])

    def test_allows_clean_professional_email(self):
        body = (
            "Hi [First Name],\n\n"
            "The mesh radio covers the licensed spectrum bands you mentioned. "
            "Want me to send you the spec sheet for your project?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertEqual(v, [])

    def test_multiple_violations(self):
        v = check_tone("Don't be stupid, this is a limited time offer and you must act now")
        self.assertTrue(len(v) >= 2)

    def test_catches_tracking_data_download(self):
        body = (
            "Hi [First Name],\n\n"
            "Since you've already downloaded the RADIO-B platform brochure, "
            "I thought I'd follow up. Want the spec sheet too?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "TRACKING_DATA" for x in v))

    def test_catches_tracking_data_download_noun_first(self):
        """'the brochure you downloaded' — noun before 'you downloaded' must also be caught."""
        body = (
            "Hi [First Name],\n\n"
            "The RADIO-B brochure you downloaded is a good starting point. "
            "Want me to send the full spec sheet?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "TRACKING_DATA" for x in v))

    def test_catches_content_download_reference_solution_brief(self):
        """Implying knowledge of gated content access — 'since you accessed our solution brief'."""
        body = (
            "Hi [First Name],\n\n"
            "Since you accessed our solution brief on mesh radio, "
            "I wanted to share the configuration guide. Want me to send it?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "TRACKING_DATA" for x in v))

    def test_catches_content_download_reference_interest_in(self):
        """'your interest in our white paper' implies download tracking."""
        body = (
            "Hi [First Name],\n\n"
            "Your interest in our white paper tells me you are evaluating options. "
            "Want to discuss further?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "TRACKING_DATA" for x in v))

    def test_catches_call_request_gerund(self):
        body = (
            "Hi [First Name],\n\n"
            "I can look into connecting you with Alex on our team. "
            "Are you evaluating options now?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "CALL_REQUEST" for x in v))

    def test_catches_on_our_team(self):
        body = (
            "Hi [First Name],\n\n"
            "I'd like to introduce you to Sarah on our team who handles these deployments. "
            "Does that work?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "CALL_REQUEST" for x in v))

    def test_catches_rfq_received_with_model_numbers(self):
        body = (
            "Hi [First Name],\n\n"
            "Your RFQ for the RADIO-A100-X and the RADIO-D700 units at 7 GHz "
            "has been received and I am following up. "
            "Are you ready to move forward?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "GATEKEEPER" for x in v))

    def test_catches_non_english_arabic(self):
        body = (
            "Hi [First Name],\n\n"
            "مرحباً، أود معرفة المزيد عن منتجاتكم. "
            "Are you evaluating options?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "NON_ENGLISH" for x in v))

    def test_catches_non_english_spanish(self):
        body = (
            "Hi [First Name],\n\n"
            "Gracias por su interés en nuestros productos de telecomunicaciones. "
            "Estamos encantados de poder ayudarle con su consulta.\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "NON_ENGLISH" for x in v))

    def test_catches_pii_email_address(self):
        body = (
            "Hi [First Name],\n\n"
            "Please reply to sender@example.com if you need anything. "
            "Are you ready to move forward?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "PII_EMAIL" for x in v))

    def test_catches_pii_phone_number(self):
        body = (
            "Hi [First Name],\n\n"
            "You can also reach us at +1-972-555-0100. "
            "Are you evaluating options now?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "PII_PHONE" for x in v))

    def test_catches_pii_deal_value_dollar(self):
        body = (
            "Hi [First Name],\n\n"
            "We completed an $8M private network modernization for a similar utility. "
            "Are you at the spec stage already?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "PII_DEAL_VALUE" for x in v))

    def test_catches_pii_deal_value_currency_code(self):
        body = (
            "Hi [First Name],\n\n"
            "The typical deployment runs 2.5M EUR for a network of this scale. "
            "Want me to send the spec sheet?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "PII_DEAL_VALUE" for x in v))

    def test_allows_product_model_numbers(self):
        """Product codes like RADIO-A100, RADIO-C8010, 7 GHz should not trigger PII checks."""
        body = (
            "Hi [First Name],\n\n"
            "The RADIO-A100 and RADIO-C8010 cover the 7 GHz band you mentioned. "
            "Are you at the spec stage already?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertFalse(any(x["label"] in ("PII_EMAIL", "PII_PHONE", "PII_DEAL_VALUE") for x in v))

    def test_catches_person_name_after_contact_verb(self):
        body = (
            "Hi [First Name],\n\n"
            "Please contact Alex Pero directly and he will sort this out. "
            "Are you ready to proceed?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "PII_PERSON_NAME" for x in v))

    def test_catches_person_name_on_our_team(self):
        body = (
            "Hi [First Name],\n\n"
            "Kelly Smith on our team handles these deployments. "
            "Want me to send the spec sheet?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "PII_PERSON_NAME" for x in v))

    def test_catches_person_name_will_follow_up(self):
        body = (
            "Hi [First Name],\n\n"
            "John Smith will follow up with you on the pricing. "
            "Are you at the spec stage?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertTrue(any(x["label"] == "PII_PERSON_NAME" for x in v))

    def test_allows_company_name_titlecase(self):
        """Two TitleCase words that are a company or product should not trigger."""
        body = (
            "Hi [First Name],\n\n"
            "The mesh radio platform is widely used in similar deployments. "
            "Are you evaluating options now?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertFalse(any(x["label"] == "PII_PERSON_NAME" for x in v))

    def test_allows_placeholders(self):
        """[First Name] and [Sender Name] placeholders must never trigger."""
        body = (
            "Hi [First Name],\n\n"
            "The RADIO-A covers your backhaul requirements. "
            "Are you at the spec stage?\n\n"
            "Best,\n[Sender Name]"
        )
        v = check_tone(body, body=body)
        self.assertFalse(any(x["label"] == "PII_PERSON_NAME" for x in v))


class TestExtractEmailContent(unittest.TestCase):
    def test_extracts_subject_and_body(self):
        tool_input = {"command": 'draft-email --contact-id 123 --subject "Hello there" --body "Nice to meet you" --score 85'}
        combined, body = extract_email_content(tool_input)
        self.assertIn("Hello there", combined)
        self.assertIn("Nice to meet you", combined)
        self.assertIn("Nice to meet you", body)
        self.assertNotIn("Hello there", body)

    def test_extracts_single_quoted(self):
        tool_input = {"command": "send-email --contact-id 123 --subject 'Hi' --body 'Test body'"}
        combined, body = extract_email_content(tool_input)
        self.assertIn("Hi", combined)
        self.assertIn("Test body", combined)

    def test_returns_empty_for_non_email(self):
        tool_input = {"command": "fetch-mqls --start-date 2026-02-16"}
        combined, body = extract_email_content(tool_input)
        self.assertEqual(combined, "")
        self.assertEqual(body, "")


if __name__ == "__main__":
    unittest.main()
