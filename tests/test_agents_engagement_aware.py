"""Unit tests for engagement-aware agents — scorer and drafter see submissions."""
import unittest

from pydantic import BaseModel


class TestScorerEngagementContext(unittest.TestCase):
    def test_scorer_agent_exists(self):
        from outreach_pipeline.agents.scorer import scorer_agent
        self.assertIsNotNone(scorer_agent)

    def test_score_result_has_engagement_fields(self):
        from outreach_pipeline.agents.scorer import ScoreResult
        result = ScoreResult(
            buying_intent="high",
            intent_signals=["pricing", "timeline"],
            score=85,
            score_reasoning="Strong signals across 2 submissions",
            product_match="RADIO-A",
        )
        self.assertEqual(result.score, 85)

    def test_scorer_instructions_mention_submissions(self):
        from outreach_pipeline.agents.scorer import scorer_agent
        instructions = scorer_agent._instructions
        # Scorer should be told to consider all submissions
        self.assertTrue(any("submission" in str(i).lower() for i in instructions))


class TestDrafterEngagementContext(unittest.TestCase):
    def test_drafter_agent_exists(self):
        from outreach_pipeline.agents.drafter import drafter_agent
        self.assertIsNotNone(drafter_agent)

    def test_drafter_instructions_mention_submissions(self):
        from outreach_pipeline.agents.drafter import drafter_agent
        instructions = drafter_agent._instructions
        self.assertTrue(any("submission" in str(i).lower() for i in instructions))


class TestOrchestratorEngagementTools(unittest.TestCase):
    def test_orchestrator_has_engagement_tools(self):
        from outreach_pipeline.agents.orchestrator import ALL_TOOLS
        self.assertIn("get_engagements", ALL_TOOLS)
        self.assertIn("get_engagement_details", ALL_TOOLS)
        self.assertIn("get_contact_history", ALL_TOOLS)
        self.assertIn("score_engagement", ALL_TOOLS)

    def test_orchestrator_roles_include_engagement_tools(self):
        from outreach_pipeline.agents.orchestrator import ROLE_TOOLS
        self.assertIn("get_engagements", ROLE_TOOLS["viewer"])
        self.assertIn("get_engagement_details", ROLE_TOOLS["viewer"])
        self.assertIn("score_engagement", ROLE_TOOLS["drafter"])


if __name__ == "__main__":
    unittest.main()
