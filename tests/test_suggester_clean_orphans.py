"""Unit tests for _clean_orphans in graph/suggester.py.

The suggester runs OpenAI with a copy of the consumer agent's message
history. If either half of a tool exchange (the assistant's tool_call
or the tool response) is missing, OpenAI 400s with
`tool_call_ids did not have response messages` — and the frontend gets
no suggestions because the fetch terminates mid-stream.

These tests pin the bug fixes that landed over time:

  1. Orphan tool messages (no preceding tool_call) are dropped.
  2. Orphan assistant tool_calls (no response) are stripped; if that
     leaves the assistant empty it is dropped too.
  3. Order-independence — a tool message that appears BEFORE its
     paired assistant message in the input list must still survive
     the clean. Regression guard for the 2026-04-24 issue where
     CopilotKit streamed messages out of order and `_clean_orphans`
     dropped the tool half because `kept_call_ids` was populated
     lazily during pass 2.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from outreach_pipeline.graph.suggester import _clean_orphans


def _ai_with_tool_calls(tool_calls, content=""):
    return AIMessage(content=content, tool_calls=tool_calls)


def _tc(call_id, name="get_leads", args=None):
    return {"id": call_id, "name": name, "args": args or {}}


class TestCleanOrphans:
    def test_keeps_paired_tool_exchange(self):
        msgs = [
            HumanMessage(content="show leads"),
            _ai_with_tool_calls([_tc("call_1")]),
            ToolMessage(content="3 leads", tool_call_id="call_1"),
        ]
        out = _clean_orphans(msgs)
        assert len(out) == 3
        assert isinstance(out[-1], ToolMessage)

    def test_drops_orphan_tool_message(self):
        """Tool message without any preceding assistant.tool_call."""
        msgs = [
            HumanMessage(content="hi"),
            ToolMessage(content="stray", tool_call_id="call_orphan"),
            AIMessage(content="hello"),
        ]
        out = _clean_orphans(msgs)
        assert not any(isinstance(m, ToolMessage) for m in out)
        assert any(isinstance(m, HumanMessage) for m in out)
        assert any(isinstance(m, AIMessage) for m in out)

    def test_strips_orphan_assistant_tool_call(self):
        """Assistant tool_call with no responding tool message — strip
        the tool_call so OpenAI doesn't 400."""
        msgs = [
            HumanMessage(content="show leads"),
            _ai_with_tool_calls([_tc("call_missing")], content="let me check"),
        ]
        out = _clean_orphans(msgs)
        ai = next(m for m in out if isinstance(m, AIMessage))
        assert ai.tool_calls == []
        # Content preserved so the assistant isn't empty-dropped
        assert ai.content == "let me check"

    def test_drops_assistant_when_only_tool_calls_and_all_orphaned(self):
        """Assistant with nothing but orphan tool_calls has no signal left."""
        msgs = [
            HumanMessage(content="q"),
            _ai_with_tool_calls([_tc("call_x")]),  # no content, no response
        ]
        out = _clean_orphans(msgs)
        assert not any(isinstance(m, AIMessage) for m in out)

    def test_strips_one_tool_call_keeps_another(self):
        """Mixed case: assistant calls two tools; only one response came
        back. Keep the answered one, strip the orphan."""
        msgs = [
            HumanMessage(content="q"),
            _ai_with_tool_calls([
                _tc("call_1", name="get_leads"),
                _tc("call_2", name="get_drafts"),
            ]),
            ToolMessage(content="3 leads", tool_call_id="call_1"),
        ]
        out = _clean_orphans(msgs)
        ai = next(m for m in out if isinstance(m, AIMessage))
        assert len(ai.tool_calls) == 1
        assert ai.tool_calls[0]["id"] == "call_1"
        # Tool message for call_1 survives
        assert any(
            isinstance(m, ToolMessage) and m.tool_call_id == "call_1" for m in out
        )

    def test_out_of_order_tool_message_is_dropped(self):
        """Regression (2026-04-24): a tool message that precedes its
        paired assistant message in the list is ALSO invalid for
        OpenAI — its rule is 'tool role must respond to a preceding
        tool_calls', not just 'both halves present somewhere'. When
        CopilotKit's streaming clone reshuffles the order, the tool
        message must be dropped AND the assistant's tool_call stripped
        (since now there's no valid response). Verified live against
        staging: without this, POSTing such a list to
        `/copilotkit-suggester` returns 400 with 'messages with role
        tool must be a response to a preceeding message with
        tool_calls'."""
        msgs = [
            HumanMessage(content="q"),
            ToolMessage(content="3 leads", tool_call_id="call_1"),
            _ai_with_tool_calls([_tc("call_1")]),
        ]
        out = _clean_orphans(msgs)
        # Out-of-order tool message is dropped
        assert not any(isinstance(m, ToolMessage) for m in out)
        # And the assistant's now-unanswered tool_call was also a
        # tool-call-only message with no content, so the whole
        # assistant is dropped too
        assert not any(isinstance(m, AIMessage) for m in out)
        # Only the user message remains
        assert [type(m).__name__ for m in out] == ["HumanMessage"]

    def test_strips_additional_kwargs_tool_calls_too(self):
        """Regression (2026-04-24, second suggester 400 in prod):
        langchain_openai's chat-model adapter serializes assistant
        tool_calls from BOTH `AIMessage.tool_calls` (simplified shape)
        AND `AIMessage.additional_kwargs['tool_calls']` (raw OpenAI
        shape populated on every real LLM response). Stripping only
        `tool_calls` leaves the orphan alive in `additional_kwargs` and
        OpenAI still 400s with `tool_call_ids did not have response
        messages`. Verified against live staging: stripping both
        surfaces is required. Unit tests using _ai_with_tool_calls()
        alone don't exercise this path because the helper leaves
        additional_kwargs empty — this test builds the production
        shape explicitly."""
        raw_orphan = {
            "id": "call_orphan",
            "type": "function",
            "function": {"name": "get_leads", "arguments": "{}"},
        }
        raw_kept = {
            "id": "call_kept",
            "type": "function",
            "function": {"name": "get_drafts", "arguments": "{}"},
        }
        ai = AIMessage(
            content="let me check",
            tool_calls=[
                _tc("call_orphan", name="get_leads"),
                _tc("call_kept", name="get_drafts"),
            ],
            additional_kwargs={"tool_calls": [raw_orphan, raw_kept]},
        )
        msgs = [
            HumanMessage(content="q"),
            ai,
            ToolMessage(content="2 drafts", tool_call_id="call_kept"),
        ]
        out = _clean_orphans(msgs)
        ai_out = next(m for m in out if isinstance(m, AIMessage))
        # Simplified tool_calls only has the kept one
        assert [tc["id"] for tc in ai_out.tool_calls] == ["call_kept"]
        # CRITICAL: raw additional_kwargs tool_calls must be pruned too
        akw_tcs = (ai_out.additional_kwargs or {}).get("tool_calls") or []
        assert [tc["id"] for tc in akw_tcs] == ["call_kept"]

    def test_fully_orphan_assistant_clears_additional_kwargs(self):
        """When every tool_call is orphaned and the assistant has
        content worth keeping, `.tool_calls` is set to [] AND
        `additional_kwargs['tool_calls']` is fully removed — otherwise
        langchain_openai falls back to it and re-injects the orphans."""
        raw_orphan = {
            "id": "call_X",
            "type": "function",
            "function": {"name": "get_leads", "arguments": "{}"},
        }
        ai = AIMessage(
            content="let me check",
            tool_calls=[_tc("call_X")],
            additional_kwargs={"tool_calls": [raw_orphan]},
        )
        out = _clean_orphans([HumanMessage(content="q"), ai])
        ai_out = next(m for m in out if isinstance(m, AIMessage))
        assert ai_out.tool_calls == []
        assert "tool_calls" not in (ai_out.additional_kwargs or {})
        assert ai_out.content == "let me check"

    def test_empty_input(self):
        assert _clean_orphans([]) == []

    def test_no_tool_exchanges_passthrough(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content="hello"),
            HumanMessage(content="how are you"),
        ]
        out = _clean_orphans(msgs)
        assert len(out) == 3
        assert all(not getattr(m, "tool_calls", None) for m in out)
