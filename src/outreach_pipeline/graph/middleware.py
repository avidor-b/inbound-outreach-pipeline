"""Shared middleware for specialist subgraph agents.

ScopeMiddleware: scopes model view to current turn + memo, repairs orphaned messages.
ReviewMiddleware: extends ScopeMiddleware with HITL hooks for draft approval and
                  delete confirmation. Validation failures are invisible to the user —
                  the middleware silently re-drafts via LLM until tone checks pass.
"""
from __future__ import annotations

import json

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

MAX_VALIDATION_RETRIES = 3


class ScopeMiddleware(AgentMiddleware):
    """Scope model's view to [memo + current request + within-turn tool calls].

    request.override(state=...) changes only what the model sees,
    NOT the actual graph state.
    Also repairs orphaned ToolMessages from CopilotKit frontend handling.
    """

    @staticmethod
    def _repair(messages: list) -> list:
        """Repair orphaned tool calls/results in the message history.

        Handles both LangChain message objects and dict-format messages.
        Adds placeholder ToolMessages for any AIMessage tool_calls that
        don't have a matching ToolMessage response — prevents OpenAI
        400 errors ("tool_call_ids did not have response messages").
        """
        pending_ids: set[str] = set()
        answered_ids: set[str] = set()

        for m in messages:
            # Handle both object and dict formats
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls is None and isinstance(m, dict):
                tool_calls = m.get("tool_calls", [])
            for tc in (tool_calls or []):
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id:
                    pending_ids.add(tc_id)

            if isinstance(m, ToolMessage) and hasattr(m, "tool_call_id"):
                answered_ids.add(m.tool_call_id)
            elif isinstance(m, dict) and m.get("role") == "tool":
                tcid = m.get("tool_call_id", "")
                if tcid:
                    answered_ids.add(tcid)

        orphan_tool_call_ids = pending_ids - answered_ids

        repaired: list = []
        for m in messages:
            # Drop orphaned ToolMessages (no matching tool_call)
            if isinstance(m, ToolMessage):
                tcid = getattr(m, "tool_call_id", "")
                if tcid and tcid not in pending_ids:
                    continue
            elif isinstance(m, dict) and m.get("role") == "tool":
                tcid = m.get("tool_call_id", "")
                if tcid and tcid not in pending_ids:
                    continue

            repaired.append(m)

            # Add placeholder responses for orphaned tool_calls
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls is None and isinstance(m, dict):
                tool_calls = m.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                    if tc_id in orphan_tool_call_ids:
                        repaired.append(ToolMessage(
                            content="[Cancelled]",
                            tool_call_id=tc_id,
                        ))
                        orphan_tool_call_ids.discard(tc_id)

        return repaired

    def _scope_messages(self, state: dict) -> list:
        """Prepend memo and repair orphaned tool messages.

        All messages are passed through — the router already trims old turns
        and strips internal tool calls. No additional scoping here.
        """
        messages = state.get("messages", [])
        memo = state.get("memo", "")

        scoped = []
        if memo:
            scoped.append(SystemMessage(content=f"[Working memo]\n{memo}"))
        scoped.extend(messages)
        return self._repair(scoped)

    def wrap_model_call(self, request, handler):
        scoped = self._scope_messages(request.state)
        request = request.override(
            state={**request.state, "messages": scoped}
        )
        return handler(request)

    async def awrap_model_call(self, request, handler):
        scoped = self._scope_messages(request.state)
        request = request.override(
            state={**request.state, "messages": scoped}
        )
        return await handler(request)

    @staticmethod
    def _extract_ids_from_messages(messages: list) -> str:
        """Scan ToolMessages for JSON results containing entity lists.

        Returns a compact summary like:
          Contacts: contact-example-001 (Example Systems), contact-example-002 (Example Retail)
          Engagements: 142 (open, 73/100), 143 (scoring)

        Empty string if no entities found.
        """
        import json as _json

        contacts: list[str] = []
        engagements: list[str] = []
        seen: set[str] = set()

        for m in messages:
            if not isinstance(m, ToolMessage):
                continue
            content = getattr(m, "content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            # Find the JSON payload (may have an ID summary prefix)
            json_start = content.find("{")
            if json_start < 0:
                json_start = content.find("[")
            if json_start < 0:
                continue
            try:
                data = _json.loads(content[json_start:])
            except (_json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue

            # Extract contacts/leads — some tools may return these
            # keys as counts (int) rather than lists, so type-check.
            for key in ("leads", "contacts"):
                items = data.get(key, [])
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    cid = str(item.get("id", item.get("contact_id", "")))
                    company = item.get("company", "")
                    if cid and cid not in seen:
                        seen.add(cid)
                        contacts.append(f"{cid} ({company})" if company else cid)

            # Extract from single contact result
            contact = data.get("contact")
            if isinstance(contact, dict):
                cid = str(contact.get("id", ""))
                company = (contact.get("properties") or {}).get("company", "")
                if cid and cid not in seen:
                    seen.add(cid)
                    contacts.append(f"{cid} ({company})" if company else cid)

            # Extract engagements — same type guard as above.
            engs = data.get("engagements", [])
            if not isinstance(engs, list):
                engs = []
            for eng in engs:
                if not isinstance(eng, dict):
                    continue
                eid = str(eng.get("id", eng.get("engagement_id", "")))
                status = eng.get("status", "")
                score = eng.get("score", "")
                if eid and eid not in seen:
                    seen.add(eid)
                    label = status
                    if score:
                        label += f", {score}/100"
                    engagements.append(f"{eid} ({label})" if label else eid)

        lines = []
        if contacts:
            lines.append("Contacts: " + ", ".join(contacts))
        if engagements:
            lines.append("Engagements: " + ", ".join(engagements))
        return "\n".join(lines)

    def after_agent(self, state, runtime):
        return self._auto_memo(state)

    async def aafter_agent(self, state, runtime):
        return self._auto_memo(state)

    def _auto_memo(self, state: dict) -> dict:
        """Auto-extract entity IDs from this turn's tool results and
        prepend them to the memo so they're visible in the next turn.

        Uses a [CONTEXT] marker so repeated runs replace rather than
        accumulate.
        """
        messages = state.get("messages", [])
        ids_block = self._extract_ids_from_messages(messages)
        if not ids_block:
            return {}

        memo = state.get("memo", "")
        # Replace existing [CONTEXT] block if present
        marker_start = "[CONTEXT]"
        marker_end = "[/CONTEXT]"
        if marker_start in memo:
            before = memo[:memo.index(marker_start)]
            after_marker = memo.find(marker_end)
            after = memo[after_marker + len(marker_end):] if after_marker >= 0 else ""
            memo = before.strip() + "\n" + after.strip()
            memo = memo.strip()

        new_block = f"{marker_start}\n{ids_block}\n{marker_end}"
        updated = f"{new_block}\n{memo}" if memo else new_block
        return {"memo": updated}


# Re-export from draft_helpers for use in this module
from outreach_pipeline.graph.draft_helpers import (  # noqa: E402
    afix_draft_with_feedback as _afix_draft_with_feedback,
    avalidate_cta as _avalidate_cta,
    avalidate_with_retry as _avalidate_with_retry,
    afetch_draft_context as _afetch_draft_context,
    parse_resume,
)


class DraftReviewMiddleware(ScopeMiddleware):
    """ScopeMiddleware + after_agent hook for draft validation and approval.

    after_agent runs ONCE after the drafting agent completes. It:
    1. Parses subject/body from the agent's last message
    2. Validates tone (silently re-drafts via LLM if violations found)
    3. Interrupts for human approval
    4. Saves the draft if approved
    """

    @staticmethod
    def _parse_draft(text: str) -> dict:
        """Extract the FIRST draft from agent output.

        Handles both strict format (ENGAGEMENT: / SUBJECT: / body) and
        loose format (agent wrote a draft without the structured header).
        If the agent produced multiple drafts, only the first is extracted.
        """
        import re

        # Split on ENGAGEMENT: boundaries — only process the first block
        blocks = re.split(r'(?=ENGAGEMENT:\s*\d+)', text, maxsplit=2)
        first_block = blocks[1] if len(blocks) > 1 else blocks[0]

        # Strict format: ENGAGEMENT: <id>
        eid_m = re.search(r"ENGAGEMENT:\s*(\d+)", first_block)
        # Loose format: "engagement #5145", "engagement 5145", "engagement ID 5038"
        if not eid_m:
            eid_m = re.search(r"engagement\s*(?:#|ID)?\s*(\d+)", first_block, re.IGNORECASE)

        rag_m = re.search(r"RAG_CONTEXT:\s*(.+)", first_block)

        # Structured resource fields — JSON-encoded arrays so title/phrase
        # payloads can safely contain colons/quotes.
        def _parse_json_line(field: str) -> list:
            m = re.search(rf"{field}:\s*(\[.*?\])\s*$", first_block,
                          re.MULTILINE | re.DOTALL)
            if not m:
                return []
            try:
                val = json.loads(m.group(1))
                return val if isinstance(val, list) else []
            except (json.JSONDecodeError, ValueError):
                return []

        promised_resources = _parse_json_line("PROMISED_RESOURCES")
        referenced_ids = _parse_json_line("REFERENCED_IDS")
        unverified_promises = _parse_json_line("UNVERIFIED_PROMISES")

        # Subject: line (strict or loose)
        subj_m = re.search(r"SUBJECT:\s*(.+)", first_block)
        if not subj_m:
            subj_m = re.search(r"Subject:\s*(.+)", first_block)

        # Extract body
        body = ""
        if "Hi [First Name]" in first_block:
            idx = first_block.index("Hi [First Name]")
            body = first_block[idx:].strip()
        elif subj_m:
            body = first_block[subj_m.end():].strip()

        # Trim body at the second "Hi [First Name]" if agent wrote alternatives
        if body.count("Hi [First Name]") > 1:
            second = body.index("Hi [First Name]", body.index("Hi [First Name]") + 1)
            body = body[:second].strip()

        # Trim trailing agent commentary after the sign-off
        if "[Sender Name]" in body:
            idx = body.rindex("[Sender Name]") + len("[Sender Name]")
            body = body[:idx].strip()

        return {
            "engagement_id": int(eid_m.group(1)) if eid_m else 0,
            "rag_context": rag_m.group(1).strip() if rag_m else "",
            "subject": subj_m.group(1).strip() if subj_m else "",
            "body": body,
            "promised_resources": promised_resources,
            "referenced_resource_ids": referenced_ids,
            "unverified_promises": unverified_promises,
        }

    async def _areview(self, state: dict) -> dict:
        """Process ALL drafts sequentially — one interrupt per draft.

        Collects drafts from ToolMessages (structured format from draft_email)
        and AIMessages (loose format). For each draft: validate → interrupt
        for approval → save or reject → continue to next.

        Same pattern as the pipeline's draft_queue: the user sees one Draft
        Review card at a time, automatically progressing to the next.
        """
        from langgraph.types import interrupt

        messages = state.get("messages", [])

        # Collect ALL drafts from ToolMessages (preferred — structured format)
        drafts = []
        seen_eids = set()
        for m in messages:
            if isinstance(m, ToolMessage) and getattr(m, "content", ""):
                draft = self._parse_draft(m.content)
                if draft["body"] and draft["engagement_id"] not in seen_eids:
                    drafts.append(draft)
                    seen_eids.add(draft["engagement_id"])

        # Fall back to AIMessage if no ToolMessage drafts found
        if not drafts:
            for m in reversed(messages):
                if isinstance(m, AIMessage) and getattr(m, "content", ""):
                    draft = self._parse_draft(m.content)
                    if draft["body"]:
                        drafts.append(draft)
                        break

        if not drafts:
            return {}

        # Process each draft sequentially with interrupt() per draft
        results = []
        from outreach_pipeline.tools.workflow import save_engagement_draft as _save

        for draft in drafts:
            body = draft["body"]
            engagement_id = draft["engagement_id"]
            subject = draft["subject"]
            rag_context = draft["rag_context"]
            promised_resources = draft.get("promised_resources", []) or []
            referenced_ids = draft.get("referenced_resource_ids", []) or []
            unverified_promises = draft.get("unverified_promises", []) or []

            # draft_email tool already validates tone + CTA — skip here.
            # Fetch context for the approval card
            context = await _afetch_draft_context(engagement_id) if engagement_id else {}
            if rag_context:
                context["rag_sources"] = rag_context

            # Approval loop (up to 30 revision rounds per draft)
            # Token prevents stale resume values from auto-approving
            import uuid as _uuid
            approved = False
            forward_sdr = False
            for _round in range(30):
                token = str(_uuid.uuid4())[:8]
                response = interrupt({
                    "action": "approve_draft",
                    "token": token,
                    "args": {
                        "engagement_id": engagement_id,
                        "subject": subject,
                        "body": body,
                        "token": token,
                        "promised_resources": promised_resources,
                        "referenced_resource_ids": referenced_ids,
                        "unverified_promises": unverified_promises,
                        **context,
                    },
                })

                parsed = parse_resume(response)

                # Stale resume detection
                resume_token = parsed.get("token", "")
                if resume_token and resume_token != token:
                    continue

                approved = parsed.get("approved", False)
                forward_sdr = bool(parsed.get("forward_sdr", False))
                feedback = parsed.get("feedback", "")

                if approved or forward_sdr:
                    break
                if not feedback:
                    break  # rejected without feedback — skip to next draft

                body = await _afix_draft_with_feedback(body, feedback)
                body, _passed = await _avalidate_with_retry(body)
                # Body changed — re-run promise extraction to keep the
                # UI badges / warning consistent with the latest text.
                try:
                    from outreach_pipeline.tools.resource_catalog import (
                        extract_promises,
                    )
                    referenced_ids, unverified_promises = extract_promises(
                        body, promised_resources,
                    )
                except Exception:
                    pass

            if approved:
                await _save(
                    engagement_id, subject, body, rag_context=rag_context,
                    draft_status="approved",
                    promised_resources=promised_resources,
                    referenced_resource_ids=referenced_ids,
                    unverified_promises=unverified_promises,
                )
                # Advance the engagement too — the human has approved,
                # so the engagement is no longer "pending_approval".
                # Without this, engagement.status lags at
                # pending_approval forever while the draft shows
                # approved, which surfaces as inconsistent state in
                # the drafts card and blocks the `approved → sent`
                # transition the Mark-as-sent action needs.
                try:
                    from outreach_pipeline.db.engagements import update_status
                    await update_status(engagement_id, "approved")
                except Exception:
                    # Non-fatal — the draft is saved either way. Most
                    # common failure: engagement already past approved
                    # (e.g. a prior reviewer already progressed it).
                    pass
                results.append(f"#{engagement_id} approved")
            elif forward_sdr:
                # Human SDR will handle this engagement manually. Save
                # the drafted body so they can see what Outreach prepared,
                # but mark the engagement as sdr_handoff so the pipeline
                # won't auto-send.
                await _save(
                    engagement_id, subject, body, rag_context=rag_context,
                    draft_status="sdr_handoff",
                    promised_resources=promised_resources,
                    referenced_resource_ids=referenced_ids,
                    unverified_promises=unverified_promises,
                )
                try:
                    from outreach_pipeline.db.engagements import update_status
                    await update_status(engagement_id, "sdr_handoff")
                except Exception:
                    pass
                results.append(f"#{engagement_id} forwarded to SDR")
            else:
                results.append(f"#{engagement_id} rejected")

        summary = ", ".join(results)
        return {"messages": [AIMessage(
            content=f"Draft review complete: {summary}."
        )]}

    def after_agent(self, state, runtime):
        # Sync fallback — won't be called by langgraph dev (async)
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self._areview(state))

    async def aafter_agent(self, state, runtime):
        return await self._areview(state)


class ReviewMiddleware(ScopeMiddleware):
    """ScopeMiddleware + HITL hooks for draft approval and delete confirmation.

    wrap_tool_call intercepts:
    - delete_contact: shows preview, interrupts for human confirmation.
    After cancel, _force_stop terminates the agent loop on the next model call.
    """

    import asyncio as _asyncio
    _delete_lock = _asyncio.Lock()
    _cancelled_contacts: set[str] = set()
    _force_stop: bool = False  # Set after cancel to end agent loop

    async def _aconfirm_delete(self, request, handler):
        args = request.tool_call.get("args", {})
        contact_id = args.get("contact_id", "")
        tool_call_id = request.tool_call.get("id", "")

        def _tool_msg(payload: dict) -> ToolMessage:
            # IMPORTANT: return ToolMessage with tool_call_id so OpenAI can
            # pair it with the originating tool_call. Returning a raw string
            # causes langgraph to wrap it as a HumanMessage, which leaves the
            # tool_call orphaned and triggers a 400 on the next model call.
            return ToolMessage(
                content=json.dumps(payload),
                tool_call_id=tool_call_id,
                name="delete_contact",
            )

        # Already cancelled — instant reject, no interrupt
        if contact_id in ReviewMiddleware._cancelled_contacts:
            return _tool_msg({
                "success": False, "cancelled": True,
                "contact_id": contact_id,
                "message": "Delete was already cancelled by user.",
            })

        # Only one delete interrupt at a time — others get rejected
        if ReviewMiddleware._delete_lock.locked():
            return _tool_msg({
                "success": False, "cancelled": True,
                "contact_id": contact_id,
                "message": "Another delete is pending. Cancelled duplicate.",
            })

        async with ReviewMiddleware._delete_lock:
            # Resolve company name → contact ID so the preview + delete
            # hit the right row. Numeric IDs and existing rows pass
            # through unchanged.
            from outreach_pipeline.db.contacts import (
                get_contact as _get_contact,
                search_contacts as _search_contacts,
            )
            existing = await _get_contact(str(contact_id))
            if not existing and contact_id and not str(contact_id).isdigit():
                matches = await _search_contacts(company=str(contact_id), limit=1)
                if matches:
                    resolved = str(matches[0]["id"])
                    # Rewrite the tool_call args so the downstream
                    # handler deletes the real row too.
                    try:
                        request.tool_call["args"]["contact_id"] = resolved
                    except Exception:
                        pass
                    contact_id = resolved

            from outreach_pipeline.tools.contacts import preview_delete_contact
            preview = await preview_delete_contact(contact_id)

            from langgraph.types import interrupt
            response = interrupt({
                "action": "confirm_delete",
                "args": {
                    "contact_id": contact_id,
                    "preview": preview,
                },
            })

            parsed = parse_resume(response)
            confirmed = parsed.get("confirmed", False)

            if not confirmed:
                ReviewMiddleware._cancelled_contacts.add(contact_id)
                ReviewMiddleware._force_stop = True
                return _tool_msg({
                    "success": False, "cancelled": True,
                    "contact_id": contact_id,
                    "message": "Delete cancelled by user.",
                })

            ReviewMiddleware._cancelled_contacts.discard(contact_id)
            return await handler(request)

    async def awrap_model_call(self, request, handler):
        # After a delete cancel, force the agent loop to end
        # by returning an AIMessage with no tool_calls.
        if ReviewMiddleware._force_stop:
            ReviewMiddleware._force_stop = False
            return AIMessage(content="Delete cancelled.")

        return await super().awrap_model_call(request, handler)

    def wrap_tool_call(self, request, handler):
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        name = request.tool_call.get("name", "")
        if name == "delete_contact":
            return await self._aconfirm_delete(request, handler)
        return await handler(request)
