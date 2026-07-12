"""Monkey-patches applied to third-party code inside the server lifespan.

Two patches against `ag_ui_langgraph`:

  1. `agui_messages_to_langchain` — the upstream helper crashes with
     JSONDecodeError when a checkpointed tool call has malformed
     arguments (streaming artefacts sometimes land partial JSON).
     Our wrapper strips the bad tool calls and retries.

  2. `LangGraphAgent.get_state_snapshot` — each graph tick emits a
     full STATE_SNAPSHOT to the browser. Without filtering, ~6
     snapshots × full OutreachState sends megabytes per run and
     freezes Firefox. The filtered version keeps only the fields
     the UI actually renders and trims list accumulators.

Both patches run inside the server lifespan — extracting them to
their own module keeps server.py focused on routing + auth.
"""
from __future__ import annotations

import json as _json
import logging


def apply_ag_ui_patches() -> None:
    """Install the ag_ui_langgraph patches. Safe to call more than
    once — rebinding the same function is a no-op."""
    _patch_agui_messages_to_langchain()
    _patch_get_state_snapshot()


# ---------------------------------------------------------------------------
# (1) JSON-tolerant message normalizer
# ---------------------------------------------------------------------------

def _patch_agui_messages_to_langchain() -> None:
    import ag_ui_langgraph.utils as _utils
    import ag_ui_langgraph.agent as _agent_mod
    _orig = _utils.agui_messages_to_langchain

    def _safe(messages):
        """Strip messages whose tool_call args can't be parsed as JSON,
        then retry. Fixes streaming-artefact JSONDecodeError during
        multi-turn resume."""
        try:
            return _orig(messages)
        except _json.JSONDecodeError:
            cleaned = []
            for m in messages:
                if hasattr(m, "tool_calls") and m.tool_calls:
                    good_tcs = []
                    for tc in m.tool_calls:
                        if hasattr(tc, "function") and tc.function.arguments:
                            try:
                                _json.loads(tc.function.arguments)
                                good_tcs.append(tc)
                            except _json.JSONDecodeError:
                                # drop this tool call
                                pass
                        else:
                            good_tcs.append(tc)
                    if good_tcs:
                        m.tool_calls = good_tcs
                        cleaned.append(m)
                    # else: drop the message entirely
                else:
                    cleaned.append(m)
            return _orig(cleaned)

    _utils.agui_messages_to_langchain = _safe
    # ag_ui_langgraph.agent imports the symbol at module scope; rebind
    # the already-imported reference too so both access paths see the
    # patched function.
    _agent_mod.agui_messages_to_langchain = _safe


# ---------------------------------------------------------------------------
# (2) STATE_SNAPSHOT filter
# ---------------------------------------------------------------------------

def _patch_get_state_snapshot() -> None:
    import ag_ui_langgraph.agent as _agent_mod
    _orig = _agent_mod.LangGraphAgent.get_state_snapshot
    log = logging.getLogger("outreach")

    def _filtered(self, state):
        snapshot = _orig(self, state)

        # Turn-aware message filtering. Historical messages (before the
        # last HumanMessage) are KEPT so thread reload renders the full
        # chat. Current-turn tool messages are STRIPPED — the UI already
        # rendered them live from TOOL_CALL_START/END events, so keeping
        # them in the snapshot would show duplicates.
        if "messages" in snapshot:
            from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
            msgs = snapshot["messages"]

            # Find the last HumanMessage — start of current turn
            last_human_idx = -1
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                is_human = isinstance(m, HumanMessage) or (
                    isinstance(m, dict) and m.get("type", m.get("role", "")) == "human"
                )
                if is_human:
                    last_human_idx = i
                    break

            # Keep everything before the last HumanMessage as-is. Filter
                # only the current turn (after and including last_human_idx).
            if last_human_idx >= 0:
                historical = msgs[:last_human_idx]
                current_turn = []
                for m in msgs[last_human_idx:]:
                    if isinstance(m, ToolMessage):
                        continue
                    if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                        continue
                    if isinstance(m, dict):
                        mtype = m.get("type", m.get("role", ""))
                        if mtype == "tool":
                            continue
                        if mtype in ("ai", "assistant") and m.get("tool_calls"):
                            continue
                    current_turn.append(m)
                snapshot["messages"] = historical + current_turn

        # 2. Slim current_engagement — UI only renders a few fields
        if snapshot.get("current_engagement"):
            eng = snapshot["current_engagement"]
            snapshot["current_engagement"] = {
                k: eng[k]
                for k in (
                    "engagement_id", "contact_id", "company", "status",
                    "score", "score_reasoning", "product_match",
                    "buying_intent", "classification",
                )
                if k in eng
            }

        # 3. Trim pipeline_progress — accumulates via operator.add, last 10 enough
        if snapshot.get("pipeline_progress"):
            snapshot["pipeline_progress"] = snapshot["pipeline_progress"][-10:]

        # 4. Drop fields the UI never reads. `memo` stays — the
        # suggestions hook parses its [CONTEXT] block to produce
        # follow-up hints with real entity IDs.
        snapshot.pop("tools", None)

        log.debug(
            "STATE_SNAPSHOT keys=%s sizes=%s",
            list(snapshot.keys()),
            {k: len(str(v)) for k, v in snapshot.items()},
        )
        return snapshot

    _agent_mod.LangGraphAgent.get_state_snapshot = _filtered
