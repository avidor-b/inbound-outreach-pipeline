"""Scoring subagent — deterministic scoring with LLM judgment only.

Uses the same pipeline scoring logic (_ascore_engagement) for consistent results.
The agent's job is just to identify WHICH engagements to score — the actual scoring
is deterministic code with structured LLM output.
"""
from langchain.agents import create_agent
from langchain_core.tools import tool

from outreach_pipeline.graph.agent import _llm, DATE_HANDLING_INSTRUCTIONS, MEMO_INSTRUCTIONS
from outreach_pipeline.graph.middleware import ScopeMiddleware
from outreach_pipeline.graph.state import OutreachState
from outreach_pipeline.graph.tools import (
    get_agent_tools, search_contacts, get_engagements, update_memo,
)


async def _contact_label_for(eng: dict) -> tuple[str, str]:
    """Return (company_name, latest_contact_message_first_line) for a
    given engagement row. Used by skipped/blocked score paths so the
    card can show WHY a submission was kicked out.

    Pipe character is stripped from the message — the UI's suffix
    parser splits on `|`, so any `|` in raw text would corrupt the
    next suffix it tries to pop.
    """
    from outreach_pipeline.db.contacts import get_contact
    from outreach_pipeline.db.engagements import get_submissions as _get_subs

    company = ""
    try:
        c = await get_contact(eng.get("contact_id", ""))
        if c:
            company = (c.get("properties") or {}).get("company", "") or ""
    except Exception:
        pass

    message = ""
    try:
        subs = await _get_subs(eng.get("id", 0))
        for s in subs:
            m = (s.get("message") or "").strip()
            if m:
                message = m
                break
    except Exception:
        pass
    # One-line, capped; replace `|` so the UI's suffix popper isn't
    # confused by a pipe inside the contact's text.
    if message:
        message = message.replace("\n", " ").replace("|", "/")[:280]
    return company, message


async def _score_one(engagement_id: int, force: bool = False):
    """Core scoring logic — called by both the single and batch tools."""
    from outreach_pipeline.tools.workflow import _resolve_engagement
    eng = await _resolve_engagement(engagement_id)
    if not eng:
        return {"error": f"ID {engagement_id} not found as engagement, contact, or submission."}

    eid = eng["id"]
    status = eng.get("status", "")
    existing_score = eng.get("score", 0)

    if existing_score > 0 and not force:
        # Return the FULL existing breakdown so the UI card can render
        # the dimension bars + per-dimension evidence (same shape as a
        # fresh score). Previously this returned a terse "already
        # scored" line, which made the card collapse to an empty state.
        product = eng.get("primary_product", "")
        reasoning = eng.get("score_reasoning", "")
        company = ""
        try:
            from outreach_pipeline.db.contacts import get_contact
            c = await get_contact(eng.get("contact_id", ""))
            if c:
                company = (c.get("properties") or {}).get("company", "")
        except Exception:
            pass
        prefix = f"[{company}] " if company else ""
        tail = f". {reasoning}" if reasoning else ""
        return (
            f"{prefix}#{eid} already scored {existing_score}/100 "
            f"({product}) → {status}{tail}"
        )

    # Terminal blocked engagements are never scored — they're dead
    # weight. `force` cannot override this; un-block the engagement
    # first if you really need to re-score.
    #
    # Even for skipped/blocked cards the UI needs the company name
    # and the contact's original message so a reviewer can tell WHY
    # it's off-topic/support/etc. — the bare block code isn't enough.
    company, message = await _contact_label_for(eng)
    prefix = f"[{company}] " if company else ""
    suffix = f" |contact_message:{message}" if message else ""
    if eng.get("block_terminal") or status == "blocked":
        block_code = eng.get("block_code", "")
        block_reason = eng.get("block_reason", "")
        if block_reason:
            return f"{prefix}#{eid} blocked ({block_code}): {block_reason}{suffix}"
        return f"{prefix}#{eid} skipped — status is '{status}'{suffix}"

    if status not in ("open", "scoring", "drafting") and not force:
        return f"{prefix}#{eid} skipped — status is '{status}'{suffix}"

    # Reset score before re-scoring so _ascore_engagement runs fresh
    if force and existing_score > 0:
        from outreach_pipeline.db.pool import get_pool
        pool = get_pool()
        await pool.execute(
            "UPDATE engagements SET score=0, score_reasoning='', primary_product='' WHERE id=$1",
            eid,
        )

    from outreach_pipeline.graph.workflows.full_pipeline import _ascore_engagement
    await _ascore_engagement(eid)

    from outreach_pipeline.db.engagements import get_engagement as _get_eng
    updated = await _get_eng(eid)
    score = updated.get("score", 0) if updated else 0
    product = updated.get("primary_product", "") if updated else ""
    new_status = updated.get("status", "") if updated else ""
    reasoning = updated.get("score_reasoning", "") if updated else ""
    # Include company name so the UI card can display it
    company = ""
    industry = ""
    region = ""
    try:
        from outreach_pipeline.db.contacts import get_contact
        c = await get_contact(updated.get("contact_id", "")) if updated else None
        if c:
            props = c.get("properties") or {}
            company = props.get("company", "") or ""
            industry = props.get("industry", "") or ""
            region = props.get("region", "") or ""
    except Exception:
        pass
    prefix = f"[{company}] " if company else ""
    base = f"{prefix}#{eid} scored {score}/100 ({product}) → {new_status}. {reasoning}"
    # Meta suffixes — order must stay industry → region so the UI
    # can pop them right-to-left.
    if industry:
        base = f"{base} |industry:{industry}"
    if region:
        base = f"{base} |region:{region}"
    return base


@tool
async def score_engagement(engagement_id: int, force: bool = False) -> dict:
    """Score an engagement using the pipeline's deterministic scoring logic.

    Runs: policy check → context assembly → LLM judgment (structured output) → save.
    Smart ID resolution: accepts engagement_id, contact_id, or submission_id.
    Set force=True to re-score an already-scored engagement.
    """
    return await _score_one(engagement_id, force)


@tool
async def score_contacts(contacts: str, force: bool = False) -> str:
    """Score engagements for MULTIPLE contacts in one call.

    contacts: comma-separated company names or contact IDs.
    Example: score_contacts(contacts="Example Systems, Example Retail")

    Finds all engagements for each contact and scores them.
    Set force=True to re-score already-scored engagements.
    Use this instead of calling score_engagement multiple times.
    """
    from outreach_pipeline.db.contacts import search_contacts as _search
    from outreach_pipeline.tools.engagements import get_engagements as _get_eng

    names = [n.strip() for n in contacts.split(",") if n.strip()]
    if not names:
        return "No contacts specified."

    import json as _json, re
    results = []
    for name in names:
        # Resolve to contact_id
        if name.isdigit():
            cid = name
        else:
            matches = await _search(company=name, limit=1)
            if not matches:
                results.append({"contact": name, "error": f"No contact found for '{name}'"})
                continue
            cid = str(matches[0]["id"])

        # Pull the identity fields the UI shows alongside the score.
        # Region is stored; jobtitle/country are PII-on-demand.
        from outreach_pipeline.db.contacts import get_contact as _get_contact
        contact_row = await _get_contact(cid)
        contact_props = (contact_row or {}).get("properties", {}) or {}
        region = contact_props.get("region", "") or ""
        industry = contact_props.get("industry", "") or ""
        jobtitle = ""
        country = ""
        try:
            from outreach_pipeline.tools.pii import afetch_contact_pii
            pii = await afetch_contact_pii(cid, ["jobtitle", "country"])
            jobtitle = (pii.get("jobtitle") or "").strip()
            country = (pii.get("country") or "").strip()
        except Exception:
            pass

        # Get engagements
        eng_result = await _get_eng(contact_id=cid)
        engs = eng_result.get("engagements", [])
        for eng in engs:
            eid = eng.get("id")
            if not eid:
                continue
            try:
                text = str(await _score_one(eid, force=force))
                # Strip end-anchored meta suffixes (|contact_message,
                # |region, |industry) before the reasoning regex runs
                # so they don't get swallowed into it.
                msg_m = re.search(r"\s*\|contact_message:([^|]+?)\s*$", text)
                contact_message = msg_m.group(1).strip() if msg_m else ""
                if msg_m:
                    text = text[: msg_m.start()].rstrip()
                region_m2 = re.search(r"\s*\|region:([^|]+?)\s*$", text)
                if region_m2:
                    text = text[: region_m2.start()].rstrip()
                industry_m2 = re.search(r"\s*\|industry:([^|]+?)\s*$", text)
                if industry_m2:
                    text = text[: industry_m2.start()].rstrip()
                # Parse the score result string. reasoning may span
                # multiple lines (one bullet per dimension), so the
                # capture has to cross newlines — DOTALL via [\s\S].
                company_m = re.match(r"\[([^\]]+)\]\s*", text)
                company_from_prefix = company_m.group(1) if company_m else ""
                eid_m = re.search(r"#(\d+)", text)
                score_m = re.search(r"(\d+)/100", text)
                product_m = re.search(r"\(([^)]+)\)", text)
                status_m = re.search(r"→\s*(\w+)", text)
                reasoning_m = re.search(r"→\s*\w+\.\s+([\s\S]+)$", text)
                results.append({
                    "contact": company_from_prefix or name,
                    "engagement_id": int(eid_m.group(1)) if eid_m else eid,
                    "score": int(score_m.group(1)) if score_m else 0,
                    "primary_product": product_m.group(1) if product_m else "",
                    "status": status_m.group(1) if status_m else "",
                    "score_reasoning": reasoning_m.group(1) if reasoning_m else "",
                    "already_scored": "already scored" in text.lower(),
                    "skipped": "skipped" in text.lower() or "blocked" in text.lower(),
                    "reason": text if ("skipped" in text.lower() or "blocked" in text.lower()) else "",
                    "contact_message": contact_message,
                    "jobtitle": jobtitle,
                    "country": country,
                    "region": region,
                    "industry": industry,
                })
            except Exception as e:
                results.append({"contact": name, "engagement_id": eid, "error": str(e)})

    scored = len([r for r in results if not r.get("error") and not r.get("skipped")])
    return _json.dumps({
        "results": results,
        "count": scored,
        "total": len(results),
        "contacts": len(names),
    })


_SCORE_TOOLS = [score_engagement, score_contacts, search_contacts, get_engagements, update_memo]


score_agent = create_agent(
    model=_llm,
    tools=_SCORE_TOOLS,
    middleware=[ScopeMiddleware()],
    state_schema=OutreachState,
    system_prompt=(
        "You are the scoring agent. Score engagements using the score_engagement tool.\n\n"
        + DATE_HANDLING_INSTRUCTIONS + "\n\n"
        "Workflow:\n"
        "1. Read the [CONTEXT] block AND the chat history for ACTUAL\n"
        "   contact IDs and company names. These are real entities —\n"
        "   never invent placeholder names.\n"
        "2. For MULTIPLE contacts: call score_contacts(contacts='<Real Co 1>, <Real Co 2>, ...')\n"
        "   using the REAL company names verbatim, comma-separated.\n"
        "   NEVER pass placeholder text like 'Company A, Company B' —\n"
        "   those are documentation placeholders, not actual arguments.\n"
        "   Pass company NAMES (not numeric IDs); the tool resolves them.\n"
        "   This is ONE call that scores all their engagements. Do NOT\n"
        "   call score_engagement multiple times — use the batch tool.\n"
        "3. For a SINGLE engagement by ID: call score_engagement(engagement_id=42).\n"
        "   If the user asks to RE-SCORE, pass force=True.\n"
        "4. If the user says bare 'score them' / 'rescore' with no names,\n"
        "   scan the conversation for the most recently listed set of\n"
        "   contacts (a Leads table, enrichment cards) and use THOSE\n"
        "   names. Do not fabricate.\n"
        "5. After scoring is done, say 'Scoring complete' with a brief count.\n"
        "   Do NOT repeat scores — the UI already shows visual cards."
        + MEMO_INSTRUCTIONS
    ),
    name="scoring",
)
