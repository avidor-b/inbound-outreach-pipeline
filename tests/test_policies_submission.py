"""Unit tests for policies/submission.py — submission classification."""
import unittest

import pytest


class TestClassifyIntent(unittest.TestCase):
    def test_sales_inquiry(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Need pricing for RADIO-A units",
            form_name="Contact Us - Homepage",
            page_url="https://example.com/products/radio-a",
        )
        self.assertEqual(result["intent_type"], "sales_inquiry")
        self.assertFalse(result["support_flag"])
        self.assertFalse(result["event_flag"])

    def test_content_download_no_message(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="",
            form_name="TechSolution Brief: mesh radio",
            page_url="https://example.com/resources/solution-brief/mesh-radio",
        )
        self.assertEqual(result["intent_type"], "content_download")

    def test_support_request(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="I have some RADIO-A units and they are not working properly",
            form_name="Contact Us",
            page_url="",
        )
        self.assertEqual(result["intent_type"], "support_request")
        self.assertTrue(result["support_flag"])

    def test_event_meeting(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Would love to meet at MWC next week",
            form_name="Contact Us",
            page_url="https://example.com/events/mwc",
        )
        self.assertEqual(result["intent_type"], "event_meeting")
        self.assertTrue(result["event_flag"])

    def test_strong_support_standalone(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Need technical support for our equipment",
            form_name="Contact Us",
            page_url="",
        )
        self.assertTrue(result["support_flag"])

    def test_purchase_order_status(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Please advise on the status of PO# 12345-187 ASAP",
            form_name="Contact Us",
            page_url="",
        )
        self.assertEqual(result["intent_type"], "support_request")
        self.assertTrue(result["support_flag"])

    def test_order_status_inquiry(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Where is my order? I placed it last week",
            form_name="Contact Us",
            page_url="",
        )
        self.assertTrue(result["support_flag"])

    def test_po_with_period(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Status of P.O. #789 please",
            form_name="Contact Us",
            page_url="",
        )
        self.assertTrue(result["support_flag"])

    def test_purchase_order_spelled_out(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="What is the status of purchase order 54321?",
            form_name="Contact Us",
            page_url="",
        )
        self.assertTrue(result["support_flag"])

    def test_events_page_url(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Hello",
            form_name="Contact Us",
            page_url="https://example.com/events/gitex-2026",
        )
        self.assertTrue(result["event_flag"])

    def test_hubspot_partnerships_topic_beats_regex_sales_inquiry(self):
        """A supplier-selected Partnerships topic is authoritative even
        when the message does not match every vendor-pitch regex."""
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message=(
                "I am writing to share details regarding a new material "
                "development from our side that may be of significant "
                "interest for your mmWave sensor applications."
            ),
            form_name="Contact Us - Homepage",
            page_url="https://www.example.com/products/outdoor_radio-by-company",
            topic="Partnerships",
        )
        self.assertEqual(result["intent_type"], "vendor_pitch")
        self.assertFalse(result["support_flag"])
        self.assertFalse(result["event_flag"])

    def test_partnerships_topic_does_not_block_clear_buyer_language(self):
        """A buyer can choose the wrong HubSpot topic. Clear purchase
        language for wireless gear should not become a terminal vendor
        block just because topic=Partnerships."""
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="quero comprar radios de alta capacidad",
            form_name="Contact Us - Homepage",
            page_url="https://www.example.com/contact-us",
            topic="Partnerships",
        )
        self.assertEqual(result["intent_type"], "sales_inquiry")
        self.assertFalse(result["support_flag"])
        self.assertFalse(result["event_flag"])

    def test_hubspot_support_topic_beats_regex(self):
        """topic=Support trumps any sales-looking message text."""
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Need pricing for RADIO-A units",
            form_name="Contact Us",
            page_url="",
            topic="Support",
        )
        self.assertEqual(result["intent_type"], "support_request")
        self.assertTrue(result["support_flag"])

    def test_hubspot_careers_topic_is_off_topic(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Looking for a role in sales engineering.",
            form_name="Contact Us",
            topic="Careers",
        )
        self.assertEqual(result["intent_type"], "off_topic")

    def test_hubspot_investor_relations_topic_is_off_topic(self):
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Could you share Q3 financials?",
            form_name="Contact Us",
            topic="Investor relations",
        )
        self.assertEqual(result["intent_type"], "off_topic")

    def test_hubspot_sales_inquiry_topic_falls_through_to_regex(self):
        """topic=Sales Inquiry is the normal path — regex handles
        support / events / off-topic detection within."""
        from outreach_pipeline.policies.submission import classify_intent
        # Even with topic=Sales Inquiry, a support-flavoured message
        # still classifies as support_request (regex detects it).
        result = classify_intent(
            message="I have some RADIO-A units and they are not working properly",
            form_name="Contact Us",
            topic="Sales Inquiry",
        )
        self.assertEqual(result["intent_type"], "support_request")

    def test_empty_topic_falls_through(self):
        """No dropdown selected = regex decides. Regression guard: an
        empty string for `topic` must NOT accidentally match the
        trusted map."""
        from outreach_pipeline.policies.submission import classify_intent
        result = classify_intent(
            message="Need pricing for RADIO-A units",
            form_name="Contact Us",
            topic="",
        )
        self.assertEqual(result["intent_type"], "sales_inquiry")


class TestLLMFallback(unittest.TestCase):
    """The regex silently defaults to sales_inquiry whenever the
    support/vendor/off_topic AND-rules don't fire. That's fine when the
    message is a clean sales inquiry, but ownership can fire ("currently
    running firmware") without troubleshoot/sw_update. Those ambiguous
    zero-fire or partial-fire cases fall through to an LLM tie-breaker."""

    AMBIGUOUS_SUPPORT_MSG = (
        "We recently acquired two RADIO-F2500 units, currently running "
        "firmware version 3.2.1 build 42, and we would like to request your "
        "assistance in obtaining the latest available firmware version to keep "
        "our equipment up to date."
    )

    def test_llm_rescues_ambiguous_firmware_request_to_support(self):
        from unittest.mock import patch
        from outreach_pipeline.policies import submission
        with patch.object(submission, "_llm_classify_intent",
                          return_value="support_request") as m:
            result = submission.classify_intent(
                message=self.AMBIGUOUS_SUPPORT_MSG,
                form_name="Contact Us - Homepage",
                page_url="https://www.example.com/contact-us",
            )
        m.assert_called_once()
        self.assertEqual(result["intent_type"], "support_request")
        self.assertTrue(result["support_flag"])

    def test_zero_fire_message_calls_llm(self):
        """A generic request for a sales contact fires no regex at
        all — the regex default is a coin flip, so the LLM should be
        consulted."""
        from unittest.mock import patch
        from outreach_pipeline.policies import submission
        with patch.object(submission, "_llm_classify_intent",
                          return_value="sales_inquiry") as m:
            result = submission.classify_intent(
                message="Urgent. Need a sales person for our region. Please contact me ASAP.",
                form_name="Contact Us - Homepage",
            )
        m.assert_called_once()
        self.assertEqual(result["intent_type"], "sales_inquiry")

    def test_clean_sales_inquiry_skips_llm(self):
        """Positive regex signal (wireless + buying) = no ambiguity.
        The LLM should not be called — that's the whole point of the
        tie-breaker, we only pay when we're unsure."""
        from unittest.mock import patch
        from outreach_pipeline.policies import submission
        with patch.object(submission, "_llm_classify_intent",
                          return_value="support_request") as m:
            result = submission.classify_intent(
                message="Please quote me on 10 RADIO-A wireless backhaul links",
                form_name="Contact Us",
            )
        m.assert_not_called()
        self.assertEqual(result["intent_type"], "sales_inquiry")

    def test_llm_failure_falls_back_to_regex_default(self):
        """If the LLM is unreachable / returns garbage, the helper
        returns None and the regex's sales_inquiry decision stands.
        Degrade gracefully — no exceptions propagate to the ingest
        path."""
        from unittest.mock import patch
        from outreach_pipeline.policies import submission
        with patch.object(submission, "_llm_classify_intent",
                          return_value=None):
            result = submission.classify_intent(
                message=self.AMBIGUOUS_SUPPORT_MSG,
                form_name="Contact Us - Homepage",
            )
        self.assertEqual(result["intent_type"], "sales_inquiry")

    def test_trusted_topic_shortcuts_before_llm(self):
        """topic=Support should still win without invoking the LLM.
        Keeps the authoritative-signal path free of LLM cost."""
        from unittest.mock import patch
        from outreach_pipeline.policies import submission
        with patch.object(submission, "_llm_classify_intent") as m:
            result = submission.classify_intent(
                message=self.AMBIGUOUS_SUPPORT_MSG,
                form_name="Contact Us",
                topic="Support",
            )
        m.assert_not_called()
        self.assertEqual(result["intent_type"], "support_request")

    @pytest.mark.real_classifier_llm
    def test_helper_filters_unknown_llm_output(self):
        """The helper contract: _llm_classify_intent ONLY returns a
        value from _LLM_INTENT_LABELS or None. Anything else from the
        LLM (typos, hallucinated labels, prose) becomes None. Test it
        at the helper boundary so classify_intent can trust it."""
        from unittest.mock import patch, MagicMock
        from outreach_pipeline.policies import submission

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="nonsense_label")

        fake_agent = MagicMock()
        fake_agent._llm = mock_llm
        with patch.dict("sys.modules", {"outreach_pipeline.graph.agent": fake_agent}):
            out = submission._llm_classify_intent(
                self.AMBIGUOUS_SUPPORT_MSG, "Contact Us", "",
            )
        self.assertIsNone(out)

    @pytest.mark.real_classifier_llm
    def test_helper_accepts_valid_llm_output(self):
        from unittest.mock import patch, MagicMock
        from outreach_pipeline.policies import submission

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="  Support_Request.\n")

        fake_agent = MagicMock()
        fake_agent._llm = mock_llm
        with patch.dict("sys.modules", {"outreach_pipeline.graph.agent": fake_agent}):
            out = submission._llm_classify_intent(
                self.AMBIGUOUS_SUPPORT_MSG, "Contact Us", "",
            )
        self.assertEqual(out, "support_request")

    @pytest.mark.real_classifier_llm
    def test_helper_returns_none_on_empty_message(self):
        """Don't pay for an LLM call on an empty-body message — the
        regex already handles that via the content_download / sales_inquiry
        fork on form_name."""
        from outreach_pipeline.policies import submission
        self.assertIsNone(submission._llm_classify_intent("", "Contact Us", ""))
        self.assertIsNone(submission._llm_classify_intent("   ", "Contact Us", ""))


class TestClassifyProductFamily(unittest.TestCase):
    def test_microwave_radio(self):
        from outreach_pipeline.policies.submission import classify_product_family
        self.assertEqual(classify_product_family("Need pricing for RADIO-A100", ""), "microwave_radio")

    def test_mesh_radio(self):
        from outreach_pipeline.policies.submission import classify_product_family
        self.assertEqual(classify_product_family("Info about mesh radio", ""), "mesh_radio")

    def test_from_page_url(self):
        from outreach_pipeline.policies.submission import classify_product_family
        self.assertEqual(
            classify_product_family("Hi", "https://example.com/products/mesh-radio"),
            "mesh_radio",
        )

    def test_point_to_point_radio(self):
        from outreach_pipeline.policies.submission import classify_product_family
        self.assertEqual(classify_product_family("Quote for RADIO-C8010", ""), "point_to_point_radio")

    def test_outdoor_radio(self):
        from outreach_pipeline.policies.submission import classify_product_family
        self.assertEqual(classify_product_family("RADIO-F280 pricing", ""), "outdoor_radio")

    def test_general(self):
        from outreach_pipeline.policies.submission import classify_product_family
        self.assertEqual(classify_product_family("Tell me about your products", ""), "general")

    def test_no_message(self):
        from outreach_pipeline.policies.submission import classify_product_family
        self.assertEqual(classify_product_family("", ""), "other")


if __name__ == "__main__":
    unittest.main()
