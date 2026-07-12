"""Unit tests for agents/enricher.py — contact enrichment specialist."""
import unittest


class TestEnricherAgent(unittest.TestCase):
    def test_enricher_agent_exists(self):
        from outreach_pipeline.agents.enricher import enricher_agent
        self.assertIsNotNone(enricher_agent)

    def test_enricher_has_function_toolset(self):
        from outreach_pipeline.agents.enricher import enricher_agent
        self.assertIsNotNone(enricher_agent._function_toolset)

    def test_enrichment_result_model(self):
        from outreach_pipeline.agents.enricher import EnrichmentResult
        result = EnrichmentResult(
            company_context="LatAm telecom integrator specializing in private networks.",
            crm_context="Customer since 2020",
        )
        self.assertEqual(result.crm_context, "Customer since 2020")

    def test_enricher_deps_model(self):
        from outreach_pipeline.agents.enricher import EnricherDeps
        deps = EnricherDeps(
            scraped_content="About us: We build networks.",
            contact_properties={"company": "Acme"},
        )
        self.assertEqual(deps.contact_properties["company"], "Acme")


if __name__ == "__main__":
    unittest.main()
