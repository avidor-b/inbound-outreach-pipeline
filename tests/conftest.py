"""Shared pytest fixtures for async Postgres tests.

Uses a separate 'outreach_test' database so tests never touch production data.
"""
import pytest
import pytest_asyncio

from outreach_pipeline.db.pool import get_pool

TEST_DATABASE_URL = "postgresql://pipeline:pipeline@localhost:5432/outreach_test"

_TABLES = [
    "approvals", "messages", "artifacts", "threads", "users",
    "engagement_submissions", "send_log", "outreach_log", "pipeline_runs",
    "drafts", "replies", "do_not_contact", "scrape_blocklist",
    "engagements", "submissions", "contacts",
    "revoked_tokens",
]


@pytest.fixture
def offline_graph_model(monkeypatch):
    """Provide a tool-capable chat model for graph tests without API access."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from outreach_pipeline.graph.agents import query as query_module

    class ToolCapableFakeChatModel(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    fake = ToolCapableFakeChatModel(responses=["Synthetic graph response"])
    # Patch the proxy captured by the already-compiled specialist graph. This
    # stays correct even when tests reload graph.agent to verify lazy imports.
    monkeypatch.setattr(query_module._llm, "_instance", fake)
    return fake


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _pool():
    """Session-scoped pool — created once against outreach_test."""
    from outreach_pipeline.db.pool import init_pool, close_pool
    pool = await init_pool(database_url=TEST_DATABASE_URL)
    yield pool
    await close_pool()


@pytest_asyncio.fixture(loop_scope="session")
async def db(_pool):
    """Per-test: truncate all tables for isolation."""
    pool = get_pool()
    await pool.execute(f"TRUNCATE {', '.join(_TABLES)} CASCADE")
    yield pool


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    """Clear the per-IP rate-limit buckets between tests. Without this,
    tests that make >5 auth calls in a minute — or that share a run
    with other tests hitting /auth/* — flap with 429 because the
    buckets persist at module scope."""
    try:
        from outreach_pipeline.server import _RATE_BUCKETS
        _RATE_BUCKETS.clear()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _mute_classifier_llm_fallback(request):
    """classify_intent consults an LLM as a tie-breaker on ambiguous
    messages (zero-fire or partial-non-sales signals). For tests that
    don't want to exercise that path, stub the helper to None so the
    regex's default decision stands — which matches the classifier's
    behaviour from before the LLM fallback was introduced.

    Tests that DO want to exercise the LLM path (see TestLLMFallback
    in test_policies_submission.py) carry their own patch inside the
    test body; that override wins because it runs after this fixture.
    """
    # Opt-out via marker: @pytest.mark.real_classifier_llm
    if request.node.get_closest_marker("real_classifier_llm"):
        yield
        return
    from unittest.mock import patch
    try:
        with patch(
            "outreach_pipeline.policies.submission._llm_classify_intent",
            return_value=None,
        ):
            yield
    except (ImportError, AttributeError):
        yield
