"""Unit tests for agents/scorer.py — lead scoring specialist."""
import unittest


class TestScorerAgent(unittest.TestCase):
    def test_scorer_agent_exists(self):
        from outreach_pipeline.agents.scorer import scorer_agent
        self.assertIsNotNone(scorer_agent)

    def test_scorer_has_function_toolset(self):
        from outreach_pipeline.agents.scorer import scorer_agent
        self.assertIsNotNone(scorer_agent._function_toolset)

    def test_score_result_model(self):
        from outreach_pipeline.agents.scorer import ScoreResult
        result = ScoreResult(
            buying_intent="high",
            intent_signals=["pricing", "timeline"],
            score=85,
            score_reasoning="Strong signals",
            product_match="RADIO-B100",
        )
        self.assertEqual(result.score, 85)
        self.assertEqual(result.buying_intent, "high")

    def test_score_result_validation(self):
        from outreach_pipeline.agents.scorer import ScoreResult
        with self.assertRaises(Exception):
            ScoreResult(
                buying_intent="high", intent_signals=[], score=150,  # over 100
                score_reasoning="x",
            )

    def test_scorer_deps_model(self):
        from outreach_pipeline.agents.scorer import ScorerDeps
        deps = ScorerDeps(contact_context="Acme Corp CTO", rag_context="RADIO-B100 docs")
        self.assertEqual(deps.contact_context, "Acme Corp CTO")


if __name__ == "__main__":
    unittest.main()
