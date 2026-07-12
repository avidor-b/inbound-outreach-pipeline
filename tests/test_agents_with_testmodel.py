"""Tests for PydanticAI agents using TestModel — no API key needed.

Tests verify:
- Output types and field values (not just non-None)
- Tool invocations produce usable results
- Structured output validation
- Agent behavior with specific inputs

These tests are sync (run_sync) and don't need a database — TestModel
mocks the LLM entirely.
"""
from pydantic_ai.models.test import TestModel


class TestDrafterWithTestModel:
    def test_drafter_returns_correct_subject_and_body(self):
        from outreach_pipeline.agents.drafter import drafter_agent, DrafterDeps, MessageDraftOutput

        test_model = TestModel(custom_output_args={
            "subject": "RADIO-A for your network",
            "body": "Hi [First Name],\nThe RADIO-A platform supports your use case.\n"
                    "Want me to send the configuration guide?\nBest,\n[Sender Name]",
        })
        deps = DrafterDeps(
            contact_context="Acme Corp, VP Engineering, Telecom",
            rag_context="RADIO-A supports E1+Ethernet natively",
        )

        with drafter_agent.override(model=test_model):
            result = drafter_agent.run_sync("Draft an email for this contact", deps=deps)
            assert isinstance(result.output, MessageDraftOutput)
            assert result.output.subject == "RADIO-A for your network"
            assert "[First Name]" in result.output.body
            assert "[Sender Name]" in result.output.body
            assert result.output.body.startswith("Hi [First Name],")
            assert result.output.body.strip().endswith("[Sender Name]")

    def test_drafter_default_output_has_required_fields(self):
        """Default TestModel returns schema-valid output with all required fields."""
        from outreach_pipeline.agents.drafter import drafter_agent, DrafterDeps, MessageDraftOutput

        test_model = TestModel()
        deps = DrafterDeps()

        with drafter_agent.override(model=test_model):
            result = drafter_agent.run_sync("Draft email", deps=deps)
            assert isinstance(result.output, MessageDraftOutput)
            # subject and body are required str fields — must exist and be str
            assert isinstance(result.output.subject, str)
            assert isinstance(result.output.body, str)

    def test_drafter_with_rag_tool_produces_output(self):
        """Drafter triggers query_rag tool and still returns valid draft."""
        from outreach_pipeline.agents.drafter import drafter_agent, DrafterDeps, MessageDraftOutput

        test_model = TestModel(call_tools=['query_rag'])
        deps = DrafterDeps()

        with drafter_agent.override(model=test_model):
            result = drafter_agent.run_sync("Draft email using RAG", deps=deps)
            assert isinstance(result.output, MessageDraftOutput)
            assert isinstance(result.output.subject, str)
            assert isinstance(result.output.body, str)

    def test_drafter_usage_tracked(self):
        """Token usage is tracked even with TestModel."""
        from outreach_pipeline.agents.drafter import drafter_agent, DrafterDeps

        test_model = TestModel()
        deps = DrafterDeps()

        with drafter_agent.override(model=test_model):
            result = drafter_agent.run_sync("Draft", deps=deps)
            usage = result.usage() if callable(result.usage) else result.usage
            assert usage is not None


class TestScorerWithTestModel:
    def test_scorer_returns_correct_score_and_intent(self):
        from outreach_pipeline.agents.scorer import scorer_agent, ScorerDeps, ScoreResult

        test_model = TestModel(call_tools=[], custom_output_args={
            "buying_intent": "high",
            "intent_signals": ["pricing", "timeline"],
            "score": 85,
            "score_reasoning": "VP asking for pricing with specifics",
            "product_match": "RADIO-A",
        })
        deps = ScorerDeps(
            contact_context="Acme Corp VP Engineering asking for RADIO-A pricing",
            rag_context="RADIO-A platform documentation",
        )

        with scorer_agent.override(model=test_model):
            result = scorer_agent.run_sync("Score this engagement", deps=deps)
            assert isinstance(result.output, ScoreResult)
            assert result.output.score == 85
            assert result.output.buying_intent == "high"
            assert len(result.output.intent_signals) == 2
            assert "pricing" in result.output.intent_signals
            assert "timeline" in result.output.intent_signals
            assert result.output.product_match == "RADIO-A"
            assert len(result.output.score_reasoning) > 0

    def test_scorer_default_output_has_valid_score(self):
        from outreach_pipeline.agents.scorer import scorer_agent, ScorerDeps, ScoreResult

        test_model = TestModel(call_tools=[])
        deps = ScorerDeps()

        with scorer_agent.override(model=test_model):
            result = scorer_agent.run_sync("Score", deps=deps)
            assert isinstance(result.output, ScoreResult)
            assert isinstance(result.output.score, int)
            assert result.output.score >= 0
            assert result.output.score <= 100
            assert isinstance(result.output.buying_intent, str)
            assert isinstance(result.output.intent_signals, list)
            assert isinstance(result.output.score_reasoning, str)

    def test_scorer_with_rag_tool(self):
        from outreach_pipeline.agents.scorer import scorer_agent, ScorerDeps, ScoreResult

        test_model = TestModel(call_tools=['query_rag'])
        deps = ScorerDeps()

        with scorer_agent.override(model=test_model):
            result = scorer_agent.run_sync("Score with RAG", deps=deps)
            assert isinstance(result.output, ScoreResult)
            assert isinstance(result.output.score, int)

    def test_scorer_low_intent_output(self):
        from outreach_pipeline.agents.scorer import scorer_agent, ScorerDeps, ScoreResult

        test_model = TestModel(call_tools=[], custom_output_args={
            "buying_intent": "low",
            "intent_signals": [],
            "score": 15,
            "score_reasoning": "Generic inquiry, no product specificity",
            "product_match": "",
        })
        deps = ScorerDeps()

        with scorer_agent.override(model=test_model):
            result = scorer_agent.run_sync("Score", deps=deps)
            assert result.output.score == 15
            assert result.output.buying_intent == "low"
            assert result.output.intent_signals == []


class TestEnricherWithTestModel:
    def test_enricher_returns_company_context(self):
        from outreach_pipeline.agents.enricher import enricher_agent, EnricherDeps, EnrichmentResult

        test_model = TestModel(custom_output_args={
            "company_context": "Acme Corp is a mid-size telecom service provider "
                               "in Latin America specializing in enterprise connectivity.",
            "crm_context": "Customer since 2020",
        })
        deps = EnricherDeps(
            scraped_content="Acme Corp - Enterprise Connectivity Solutions",
            contact_properties={"company": "Acme Corp", "industry": "Telecom"},
        )

        with enricher_agent.override(model=test_model):
            result = enricher_agent.run_sync("Summarize this company", deps=deps)
            assert isinstance(result.output, EnrichmentResult)
            assert "telecom" in result.output.company_context.lower()
            assert "Latin America" in result.output.company_context
            assert result.output.crm_context == "Customer since 2020"

    def test_enricher_default_output_has_company_context(self):
        from outreach_pipeline.agents.enricher import enricher_agent, EnricherDeps, EnrichmentResult

        test_model = TestModel()
        deps = EnricherDeps()

        with enricher_agent.override(model=test_model):
            result = enricher_agent.run_sync("Enrich", deps=deps)
            assert isinstance(result.output, EnrichmentResult)
            assert isinstance(result.output.company_context, str)
            assert isinstance(result.output.crm_context, str)

    def test_enricher_with_rag_tool(self):
        from outreach_pipeline.agents.enricher import enricher_agent, EnricherDeps, EnrichmentResult

        test_model = TestModel(call_tools=['query_rag'])
        deps = EnricherDeps()

        with enricher_agent.override(model=test_model):
            result = enricher_agent.run_sync("Enrich with RAG", deps=deps)
            assert isinstance(result.output, EnrichmentResult)
