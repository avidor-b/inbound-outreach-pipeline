"""Lazy LLM proxy: the module must import without API keys present,
but the proxy must still forward real calls to the underlying client
the same way as a direct ChatOpenAI/ChatGoogleGenerativeAI instance.

The point of this is to keep /health responsive in a container whose
SSM entrypoint mis-configured the LLM provider — the agent paths will
fail when they hit the LLM, but the app itself boots.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fresh_agent_module(monkeypatch):
    """Reload graph.agent so each test observes module-import behaviour
    from scratch. Strip LLM env vars to assert lazy import doesn't
    require them."""
    for key in ("OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    module_name = "outreach_pipeline.graph.agent"
    original = sys.modules.get(module_name)
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    try:
        yield module
    finally:
        # Specialist graphs retain references to the original proxy object.
        # Restore that exact module so later tests patch the same instance.
        if original is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original


def _patch_build(module):
    """Replace _build_llm at module scope so tests can count calls
    without also stubbing every downstream LangChain method."""
    from contextlib import contextmanager
    @contextmanager
    def ctx(return_value=None):
        stub = MagicMock(name="ChatModel")
        if return_value is not None:
            stub = return_value
        with patch.object(module, "_build_llm",
                          return_value=stub) as mock_build:
            yield mock_build, stub
    return ctx


def test_module_imports_without_api_keys(fresh_agent_module):
    """Zero keys in env → module must still import cleanly."""
    assert fresh_agent_module._llm is not None
    # Proxy type, not the concrete LangChain client.
    assert type(fresh_agent_module._llm).__name__ == "_LazyLLM"


def test_real_client_not_constructed_until_first_call(fresh_agent_module):
    patch_build = _patch_build(fresh_agent_module)
    with patch_build() as (mock_build, stub):
        stub.invoke = MagicMock(return_value="result")
        # Merely holding the proxy doesn't build.
        llm = fresh_agent_module._llm
        mock_build.assert_not_called()
        # First attribute access triggers the build.
        assert llm.invoke("hi") == "result"
        mock_build.assert_called_once()


def test_subsequent_calls_reuse_same_client(fresh_agent_module):
    patch_build = _patch_build(fresh_agent_module)
    with patch_build() as (mock_build, stub):
        stub.invoke = MagicMock()
        stub.ainvoke = MagicMock()
        _ = fresh_agent_module._llm.invoke("a")
        _ = fresh_agent_module._llm.invoke("b")
        _ = fresh_agent_module._llm.ainvoke
        mock_build.assert_called_once()


def test_getattr_forwards_arbitrary_methods(fresh_agent_module):
    """LangChain's runnable interface has many methods (bind_tools,
    with_structured_output, ainvoke, astream, ...). Proxy must
    transparently expose all of them."""
    stub = MagicMock()
    stub.bind_tools.return_value = "bound"
    stub.with_structured_output.return_value = "structured"
    with patch.object(fresh_agent_module, "_build_llm", return_value=stub):
        assert fresh_agent_module._llm.bind_tools(["t"]) == "bound"
        assert fresh_agent_module._llm.with_structured_output(dict) == "structured"


def test_repr_shows_model_id_before_build(fresh_agent_module):
    r = repr(fresh_agent_module._llm)
    assert "unbuilt" in r
    # MODEL is captured at module scope so ops can see what was
    # expected even before the client is alive.
    assert "openai:" in r or "google_genai:" in r
