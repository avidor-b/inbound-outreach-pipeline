"""Pipeline StateGraph with Send fan-out for parallel enrich+score
and sequential draft queue with HITL approval.

Architecture:
    START → ingest → [Send per contact] → contact_worker → draft_queue → END

- ingest: Fetch contacts from HubSpot (idempotent)
- contact_worker: Per-contact parallel enrichment + scoring
- draft_queue: Sequential loop with interrupt() per draft for approval
"""
from __future__ import annotations

import json
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt

from outreach_pipeline.graph.draft_helpers import (
    afetch_draft_context,
    afix_draft_with_feedback,
    avalidate_cta,
    avalidate_with_retry,
    awrite_draft,
    parse_resume,
)
from outreach_pipeline.organization import get_organization_profile



# ---------------------------------------------------------------------------
# Scoring helper — code fetches context, LLM does judgment only
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field
from typing import Literal

_ORGANIZATION = get_organization_profile()


class DimensionScore(BaseModel):
    """Individual scoring dimension."""
    score: int = Field(ge=0, le=10, description="0-10 score for this dimension")
    evidence: str = Field(description="Specific data point supporting this score (max 125 chars)")


class ScoreResult(BaseModel):
    """Multi-dimension scoring output. Final score = weighted average of 5 dimensions."""
    product_fit: DimensionScore = Field(
        description=f"Is the inquiry about products {_ORGANIZATION.name} sells? 0=wrong category, 5=general fit, 10=names a configured product with specs")
    buying_intent: DimensionScore = Field(
        description="How specific is the request? 0=no message, 3='interested in products', 7=names specs/frequency, 10=requests quote with timeline")
    authority: DimensionScore = Field(
        description="Decision-making power from job title. 0=unknown/no title, 3=engineer/analyst, 7=manager/director, 10=VP/CTO/owner")
    engagement_signals: DimensionScore = Field(
        description="Digital engagement level. 0=single empty form, 3=one message, 7=multiple submissions or page views, 10=repeat visits+content downloads")
    company_fit: DimensionScore = Field(
        description="Industry and company alignment. 0=unknown/unenriched, 3=tangential industry, 7=telecom/ISP/utility, 10=operator/carrier with clear need")

    confidence: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="high=clear signals on 3+ dimensions, medium=some gaps, low=mostly unknown")
    primary_product: str = Field(default="general", description=f"Best matching {_ORGANIZATION.name} product or 'general'")

    @property
    def weighted_score(self) -> int:
        """Weighted average: product_fit 25%, buying_intent 25%,
        authority 20%, engagement 15%, company_fit 15%.

        Floor rule: if BOTH product_fit and buying_intent average >= 7
        (the prospect knows what they want AND is asking to buy), the
        final score is floored at 70. Otherwise a low authority /
        engagement / company_fit would drop a clear quote request into
        the "send a brochure" bucket — we can't respond to a BOM
        request with a marketing asset.
        """
        raw = (
            self.product_fit.score * 0.25
            + self.buying_intent.score * 0.25
            + self.authority.score * 0.20
            + self.engagement_signals.score * 0.15
            + self.company_fit.score * 0.15
        )
        score = max(1, min(100, round(raw * 10)))
        if (self.product_fit.score + self.buying_intent.score) / 2 >= 7:
            score = max(score, 70)
        return score

    @property
    def reasoning_summary(self) -> str:
        dims = [
            ("Product", self.product_fit),
            ("Intent", self.buying_intent),
            ("Authority", self.authority),
            ("Engagement", self.engagement_signals),
            ("Company", self.company_fit),
        ]
        score_line = " | ".join(f"{name}:{d.score}/10" for name, d in dims)
        # Every dimension that has evidence surfaces one line — this
        # lets the reviewer see WHY each dimension got its score rather
        # than just the top one.
        evidence_lines = []
        for name, d in dims:
            ev = (d.evidence or "").strip()
            if ev:
                evidence_lines.append(f"• {name}: {ev}")
        evidence_block = "\n".join(evidence_lines) if evidence_lines else ""
        return f"{score_line}.\n{evidence_block}" if evidence_block else score_line


SCORING_PROMPT = (
    f"You are a scoring agent for {_ORGANIZATION.name}.\n"
    f"Organization profile: {_ORGANIZATION.description}.\n"
    f"Configured products: {_ORGANIZATION.product_summary}.\n\n"
    "Score this engagement on 5 dimensions (each 0-10). The final score is a\n"
    "weighted average × 10 (so all 5s = 50, all 10s = 100).\n\n"
    "DIMENSIONS:\n"
    f"1. product_fit (25%): Is the inquiry relevant to {_ORGANIZATION.name}'s configured products?\n"
    "   0 = wrong category (printers, software). 3 = vague networking.\n"
    "   5 = general wireless. 7 = names frequency/topology.\n"
    "   10 = names a configured product with specs.\n\n"
    "2. buying_intent (25%): How specific and actionable is the request?\n"
    "   0 = no message. 3 = 'interested in products'. 5 = describes a use case.\n"
    "   7 = names specs (frequency, distance, capacity). 8 = requests pricing/quote.\n"
    "   10 = quote request + timeline + budget signal.\n"
    "   CALLBACK BOOST: if the context contains 'Callback request:' (the\n"
    "   contact gave a personal phone number AND explicitly asked to be\n"
    "   called), score buying_intent AT LEAST 8 — they're not browsing,\n"
    "   they want an SDR on the line. Cap at 10 if they ALSO describe\n"
    "   the use case or name specs.\n"
    "   ADOPTION / EXPANSION BOOST: when the message says the company\n"
    "   is PLANNING to adopt, expand, upgrade, roll out, deploy, or\n"
    "   add 5G / LTE / private wireless / wireless backhaul / mmWave\n"
    "   / microwave / fiber-replacement — that's concrete buying\n"
    "   intent, not browsing. Score buying_intent AT LEAST 7. Cap at\n"
    "   10 only if they ALSO name specs, a timeline, or a budget.\n"
    "   Phrases like 'expanding to 5G', 'rolling out private LTE',\n"
    "   'upgrading our backhaul', 'need to replace leased fiber'\n"
    "   all qualify.\n"
    "   SENIORITY × INTENT INTERACTION: if authority will score 7+\n"
    "   (manager/director/VP/C-level) AND the message describes a\n"
    "   concrete deployment, expansion, or replacement plan, score\n"
    "   buying_intent AT LEAST 8 — senior stakeholders don't fill\n"
    "   out the contact-us form to 'learn more'; they're scoping a\n"
    "   project. This stacks with the ADOPTION BOOST; pick the\n"
    "   higher floor.\n\n"
    "3. authority (20%): Decision-making power + technical influence.\n"
    "   Engineers rarely sign POs but DO spec the product — don't\n"
    "   under-score a technical contact just because they're IC-level.\n"
    "   Procurement/purchasing roles DO sign POs — don't under-score\n"
    "   them as 'unclear authority'; at a technical company they hold\n"
    "   real buying authority even if they don't pick the spec.\n"
    "   0 = no title. 2 = clearly unrelated (HR, admin, marketing).\n"
    "   3 = entry-level non-technical (intern, junior sales).\n"
    "   6 = engineer / network engineer / solutions engineer / RF /\n"
    "     architect / technical analyst — can recommend/spec vendors.\n"
    "     ALSO: procurement / purchasing / buyer / sourcing —\n"
    "     they're the ones executing the purchase order.\n"
    "   7 = senior engineer, tech lead, project lead, team lead,\n"
    "     procurement manager, category manager.\n"
    "   8 = manager / director of engineering, networks, IT, ops,\n"
    "     procurement / supply chain.\n"
    "   10 = VP / CTO / CIO / Head of Networks / owner / founder.\n\n"
    "4. engagement_signals (15%): Digital engagement level.\n"
    "   Four signal families: (A) form submissions, (B) page views,\n"
    "   (C) email engagement (opens OR clicks), (D) content downloads.\n"
    "   Count how many families are active AND how deep within each\n"
    "   (a single view vs three views is meaningfully different).\n"
    "   ANCHORS — pick the HIGHEST that fits. Do NOT round down.\n"
    "     0 = empty form, no activity.\n"
    "     2 = one submission, nothing else.\n"
    "     4 = one submission + exactly ONE other signal (just 1 view\n"
    "       OR just 1 email open).\n"
    "     6 = one submission + TWO signal families (e.g. 1 view + 1\n"
    "       open), OR one submission + multiple views in ONE family.\n"
    "     7 = one submission + multiple views + email open/click\n"
    "       (three families, with depth in at least one).\n"
    "       Also: multiple submissions on their own.\n"
    "     8 = multiple submissions + email engagement, OR single\n"
    "       submission + multiple views + content download.\n"
    "     10 = repeat visits + multiple content downloads + email\n"
    "       clicks — highly active, multi-touch.\n"
    "   EXAMPLES to anchor:\n"
    "     '1 sub + 1 view + 1 open' = 6 (three families, each shallow).\n"
    "     '1 sub + 3 views + 1 open' = 7 (three families, depth in B).\n"
    "     '1 sub + 5 views + 2 clicks + 1 download' = 9–10.\n\n"
    "5. company_fit (15%): Industry and organization alignment.\n"
    "   Score the prospect's actual need for the configured products, not\n"
    "   generic company size. Use these deployment-provided anchors:\n"
    f"{_ORGANIZATION.company_fit_guidance}\n\n"
    "RULES:\n"
    "- Each evidence field must cite ACTUAL data from the context (≤125 chars).\n"
    "  Bad: 'shows buying intent'. Good: 'Requested quote for 7GHz ATEX PTP link'.\n"
    "- If data is missing for a dimension, score 0-2 and note the gap.\n"
    f"- If the inquiry is about products outside {_ORGANIZATION.name}'s offering, product_fit = 0.\n"
    "- support_request = existing customer (cross-sell lens).\n"
    f"- primary_product: name the closest {_ORGANIZATION.name} product, or 'general'."
)


async def _ascore_engagement(engagement_id: int) -> None:
    """Fetch scoring context, call LLM (async, traced), save score."""
    from outreach_pipeline.graph.agent import _llm, INTERNAL_LLM_CONFIG
    from outreach_pipeline.tools.workflow import get_scoring_context
    from outreach_pipeline.tools.engagements import (
        check_engagement_policy,
        score_engagement as save_score,
    )
    from outreach_pipeline.db.engagements import get_engagement as _get_eng

    # 0. Never score a terminal-blocked engagement — intake already
    # made the decision, an LLM run is wasted tokens.
    eng_row = await _get_eng(engagement_id)
    if eng_row and (
        eng_row.get("block_terminal") or eng_row.get("status") == "blocked"
    ):
        return

    # 1. Check policy (DB read)
    policy = await check_engagement_policy(engagement_id)
    if policy.get("blocked"):
        reason = policy.get("reason", "Policy block")
        block_score = 15 if "event" in reason.lower() else 25
        await save_score(
            engagement_id, block_score, reason, "",
            buying_intent=policy.get("block_code", "low"),
            intent_signals=[policy.get("block_code", "policy_block")],
        )
        return

    # 2. Fetch full context (DB + RAG reads)
    ctx = await get_scoring_context(engagement_id)
    eng = ctx.get("engagement", {})
    contact = ctx.get("contact", {})
    props = contact.get("properties", {})
    subs = ctx.get("submissions", [])
    signals = ctx.get("scoring_signals", {})
    prior = ctx.get("prior_outreach", [])

    # 3. Build context text for LLM
    parts = [
        f"Engagement ID: {engagement_id}",
        f"Company: {props.get('company', 'Unknown')}",
        f"Industry: {props.get('industry', '')}",
        f"Company context: {(contact.get('company_context') or '')[:600]}",
    ]

    # Region is already stored locally (HubSpot analytics property).
    region = props.get("region", "")
    if region:
        parts.append(f"Region: {region}")

    # Job title + country are PII-on-demand (not persisted locally).
    try:
        from outreach_pipeline.tools.pii import afetch_contact_pii
        pii = await afetch_contact_pii(
            str(eng.get("contact_id", "")), ["jobtitle", "country"],
        )
        jt = (pii.get("jobtitle") or "").strip()
        country = (pii.get("country") or "").strip()
        if jt and jt != "Unknown":
            parts.append(f"Job title: {jt}")
        if country:
            parts.append(f"Country: {country}")
    except Exception:
        pass

    from outreach_pipeline.graph.draft_helpers import _page_label_from_url
    import re as _re
    # Deterministic callback detection — a contact who leaves a personal
    # phone number AND asks to be called wants a human SDR, not a
    # brochure. Surface that as a concrete fact so buying_intent picks
    # it up via the "CALLBACK BOOST" rubric rule.
    _PHONE_RE = _re.compile(r"(?:\+?\d[\s\-().]*){7,}")
    _ASK_RE = _re.compile(
        r"(?:please\s+)?(?:contact|call|reach(?:\s+out)?|ring|phone)\s+me|"
        r"my\s+(?:cell|mobile|phone|number)|"
        r"here('s|\s+is)?\s+my\s+(?:number|cell|phone)",
        _re.IGNORECASE,
    )
    callback_requested = False
    for s in subs:
        msg = s.get("message", "")
        intent = s.get("intent_type", "")
        form = s.get("form_name", "")
        url = s.get("page_url", "")
        page_label = _page_label_from_url(url)
        if msg and _ASK_RE.search(msg) and _PHONE_RE.search(msg):
            callback_requested = True
        line = f"Submission ({intent}): "
        if msg:
            line += f'"{msg[:3000]}"'
        elif page_label:
            # No free-text — give the LLM the page topic so it can
            # score intent against what the contact engaged with.
            line += f"(no message — submitted from page: {page_label})"
        if form:
            line += f" [form: {form}]"
        if url:
            line += f" [url: {url}]"
        parts.append(line)

    if callback_requested:
        parts.append(
            "Callback request: contact provided a personal phone number "
            "AND explicitly asked to be called. Apply the CALLBACK BOOST "
            "rule to buying_intent."
        )

    if signals:
        parts.append(f"Page views: {signals.get('page_views', 0)}")
        parts.append(f"Email opens: {signals.get('email_opens', 0)}")
        parts.append(f"Email clicks: {signals.get('email_clicks', 0)}")
        if signals.get("traffic_source"):
            parts.append(f"Traffic source: {signals['traffic_source']}")

    rag_matches = ctx.get("rag_matches", [])
    if rag_matches:
        for r in rag_matches[:3]:
            source = r.get("source", "")
            text = r.get("text", "")[:200]
            if text:
                parts.append(f"RAG match ({source}): {text}")

    if prior:
        parts.append(f"Prior outreach: {len(prior)} emails sent")

    context_text = "\n".join(parts)

    # 4. LLM judgment — async, so LangSmith traces it
    from langchain_core.messages import SystemMessage, HumanMessage
    structured_llm = _llm.with_structured_output(ScoreResult)
    result = await structured_llm.ainvoke([
        SystemMessage(content=SCORING_PROMPT),
        HumanMessage(content=context_text),
    ], config=INTERNAL_LLM_CONFIG)

    # 5. Compute weighted score and save
    final_score = result.weighted_score
    reasoning = result.reasoning_summary

    # Map buying_intent dimension to the legacy field
    bi_score = result.buying_intent.score
    buying_intent_label = "high" if bi_score >= 7 else "medium" if bi_score >= 4 else "low"

    # Collect evidence as intent_signals
    signals = [
        f"product_fit:{result.product_fit.score} ({result.product_fit.evidence[:60]})",
        f"buying_intent:{result.buying_intent.score} ({result.buying_intent.evidence[:60]})",
        f"authority:{result.authority.score} ({result.authority.evidence[:60]})",
        f"engagement:{result.engagement_signals.score} ({result.engagement_signals.evidence[:60]})",
        f"company_fit:{result.company_fit.score} ({result.company_fit.evidence[:60]})",
    ]

    await save_score(
        engagement_id, final_score, reasoning, result.primary_product,
        buying_intent=buying_intent_label,
        intent_signals=signals,
        score_confidence=result.confidence,
    )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class PipelineState(TypedDict):
    start_date: str
    end_date: str
    run_id: int
    contact_ids: list[str]
    ready_to_draft: Annotated[list[int], operator.add]
    draft_results: Annotated[list[dict], operator.add]
    log: Annotated[list[str], operator.add]
    # messages field required for CopilotKit AG-UI bridge compatibility —
    # state snapshots without it cause TypeError in the frontend event stream
    messages: Annotated[list[AnyMessage], add_messages]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def ingest_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Fetch contacts from HubSpot. Idempotent — duplicates skipped."""
    start = state["start_date"]
    end = state.get("end_date", start)

    from outreach_pipeline.tools.pipeline import ingest_contacts
    result = await ingest_contacts(start, end)

    contacts = result.get("contacts", [])
    new_ids = [c["contact_id"] for c in contacts if not c.get("duplicate")]

    if not new_ids:
        from outreach_pipeline.tools.contacts import get_leads
        leads = await get_leads(after=start, before=end)
        new_ids = [str(l["id"]) for l in leads.get("leads", [])]

    # Record pipeline run
    from outreach_pipeline.db.replies import start_run
    run_id = await start_run(start, end)

    return {
        "contact_ids": new_ids,
        "run_id": run_id,
        "log": [json.dumps({
            "type": "ingest",
            "fetched": result.get("fetched", 0),
            "to_process": len(new_ids),
            "date_range": f"{start} to {end}" if start != end else start,
        })],
    }


def fan_out_contacts(state: PipelineState) -> list[Send]:
    """Conditional edge: dispatch each contact to a parallel worker."""
    ids = state.get("contact_ids", [])
    if not ids:
        return [Send("draft_queue", {})]
    return [Send("contact_worker", {"contact_id": cid}) for cid in ids]


async def contact_worker(state: dict) -> dict:
    """Per-contact: enrich then score. Returns engagement IDs ready for drafting."""
    cid = state["contact_id"]
    log_parts = []

    # --- Enrich ---
    # Agent-driven enrichment: the enrich agent decides the strategy
    from outreach_pipeline.db.contacts import get_contact
    existing = await get_contact(cid)

    confidence = existing.get("enrichment_confidence", "") if existing else ""
    needs_enrichment = (
        not existing
        or not existing.get("company_context")
        or confidence == "low"
    )

    if needs_enrichment:
        try:
            props = (existing or {}).get("properties", {}) if existing else {}
            company_name = props.get("company", "")

            # Invoke the enrich agent subgraph — it decides scrape vs search vs knowledge
            from outreach_pipeline.graph.agents.enrichment import enrich_agent
            from langchain_core.messages import HumanMessage as _HM
            await enrich_agent.ainvoke({
                "messages": [_HM(content=(
                    f"Enrich contact {cid} ({company_name}). "
                    f"Find what this company does and save a summary."
                ))],
            })
            log_parts.append("enriched (agent)")
        except Exception:
            log_parts.append("enrich failed")
    else:
        cached_confidence = existing.get("enrichment_confidence", "")
        if cached_confidence:
            log_parts.append(f"already enriched (confidence={cached_confidence})")
        else:
            log_parts.append("already enriched")

    # --- Score all engagements for this contact ---
    from outreach_pipeline.tools.engagements import get_engagements
    engs_result = await get_engagements(contact_id=str(cid))
    ready = []
    scored = 0

    for eng in engs_result.get("engagements", []):
        existing_score = eng.get("score", 0)
        status = eng.get("status", "")

        if existing_score > 0:
            if existing_score >= 40 and status == "drafting":
                ready.append(eng["id"])
            continue

        if status not in ("open", "scoring"):
            continue

        try:
            await _ascore_engagement(eng["id"])
            scored += 1

            from outreach_pipeline.db.engagements import get_engagement as _get_eng_db
            updated = await _get_eng_db(eng["id"])
            if updated and updated.get("score", 0) >= 40:
                ready.append(eng["id"])
        except Exception:
            pass

    # Re-read engagements AFTER scoring to get updated scores/statuses
    engs_fresh = await get_engagements(contact_id=str(cid))

    company = ""
    if existing:
        company = (existing.get("properties") or {}).get("company", "")

    engagement_details = []
    for eng in engs_fresh.get("engagements", []):
        engagement_details.append({
            "id": eng.get("id"),
            "score": eng.get("score", 0),
            "status": eng.get("status", ""),
            "product": eng.get("primary_product", ""),
            "kind": eng.get("kind", ""),
        })

    return {
        "ready_to_draft": ready,
        "log": [json.dumps({
            "type": "contact",
            "contact_id": cid,
            "company": company,
            "enriched": "already enriched" not in " ".join(log_parts),
            "engagements": engagement_details,
            "ready_count": len(ready),
        })],
    }


async def draft_queue(state: PipelineState, config: RunnableConfig) -> dict:
    """Sequential draft loop with interrupt() per engagement.

    Each draft: generate → validate → interrupt (Draft Review card) → save.
    The frontend's useLangGraphInterrupt renders the card and resolve()
    sends Command(resume=...) as a new request for each draft.
    """
    eids = state.get("ready_to_draft", [])
    results = []

    if not eids:
        return {"draft_results": [], "log": [json.dumps({"type": "drafts", "results": []})]}

    from outreach_pipeline.tools.workflow import get_drafts, save_engagement_draft as _save

    for eid in eids:
        # Check for existing draft (idempotency on resume)
        existing = await get_drafts(engagement_id=eid)
        existing_drafts = existing.get("drafts", [])

        if existing_drafts:
            d = existing_drafts[0]
            status = d.get("status", "draft")
            if status in ("approved", "sent"):
                result_entry = {"engagement_id": eid, "status": status}
                if status == "approved":
                    result_entry["subject"] = d.get("subject", "")
                    try:
                        from outreach_pipeline.db.engagements import get_engagement as _get_eng_db2
                        from outreach_pipeline.db.contacts import get_contact as _get_contact
                        eng_rec = await _get_eng_db2(eid)
                        if eng_rec:
                            c_rec = await _get_contact(eng_rec["contact_id"])
                            result_entry["company"] = (c_rec.get("properties") or {}).get("company", "") if c_rec else ""
                    except Exception:
                        pass
                results.append(result_entry)
                continue
            if status == "draft":
                subject = d.get("subject", "")
                body = d.get("body", "")
                rag_context = d.get("rag_context", "")
            else:
                existing_drafts = None

        promised_resources: list = []
        referenced_ids: list = []
        unverified_promises: list = []

        if not existing_drafts:
            draft = await awrite_draft(eid)
            body = draft["body"]
            subject = draft["subject"]
            rag_context = draft.get("rag_context", "")
            promised_resources = draft.get("promised_resources", []) or []
            referenced_ids = draft.get("referenced_resource_ids", []) or []
            unverified_promises = draft.get("unverified_promises", []) or []

            if not body:
                results.append({"engagement_id": eid, "status": "draft_failed"})
                continue

            body, _passed = await avalidate_with_retry(body)
            from outreach_pipeline.db.engagements import get_engagement as _get_eng_for_score
            from outreach_pipeline.db.engagements import get_submissions as _get_subs_for_cta
            eng_for_cta = await _get_eng_for_score(eid)
            cta_subs = await _get_subs_for_cta(eid)
            cta_msg = next((s.get("message", "") for s in cta_subs if s.get("message")), "")
            body = await avalidate_cta(
                body, (eng_for_cta or {}).get("score", 0),
                contact_message=cta_msg,
                product=(eng_for_cta or {}).get("primary_product", ""),
            )
            # Re-extract after validators may have rewritten the body.
            try:
                from outreach_pipeline.tools.resource_catalog import extract_promises
                referenced_ids, unverified_promises = extract_promises(
                    body, promised_resources,
                )
            except Exception:
                pass

            await _save(eid, subject, body, rag_context=rag_context,
                        draft_status="draft",
                        promised_resources=promised_resources,
                        referenced_resource_ids=referenced_ids,
                        unverified_promises=unverified_promises)

        context = await afetch_draft_context(eid)
        if rag_context:
            context["rag_sources"] = rag_context

        approved = False
        for _round in range(30):
            response = interrupt({
                "action": "approve_draft",
                "args": {
                    "engagement_id": eid,
                    "subject": subject,
                    "body": body,
                    "promised_resources": promised_resources,
                    "referenced_resource_ids": referenced_ids,
                    "unverified_promises": unverified_promises,
                    **context,
                },
            })

            parsed = parse_resume(response)
            approved = parsed.get("approved", False)
            feedback = parsed.get("feedback", "")

            if approved:
                break
            if not feedback:
                break

            body = await afix_draft_with_feedback(body, feedback)
            body, _ = await avalidate_with_retry(body)
            try:
                from outreach_pipeline.tools.resource_catalog import extract_promises
                referenced_ids, unverified_promises = extract_promises(
                    body, promised_resources,
                )
            except Exception:
                pass
            await _save(eid, subject, body, rag_context=rag_context,
                        draft_status="draft",
                        promised_resources=promised_resources,
                        referenced_resource_ids=referenced_ids,
                        unverified_promises=unverified_promises)

        if approved:
            await _save(eid, subject, body, rag_context=rag_context,
                        draft_status="approved",
                        promised_resources=promised_resources,
                        referenced_resource_ids=referenced_ids,
                        unverified_promises=unverified_promises)
            results.append({
                "engagement_id": eid, "status": "approved",
                "subject": subject, "company": context.get("company", ""),
            })
        else:
            await _save(eid, subject, body, rag_context=rag_context,
                        draft_status="rejected",
                        promised_resources=promised_resources,
                        referenced_resource_ids=referenced_ids,
                        unverified_promises=unverified_promises)
            results.append({"engagement_id": eid, "status": "rejected"})

    # Finalize pipeline run record
    run_id = state.get("run_id", 0)
    if run_id:
        approved_count = sum(1 for r in results if r.get("status") == "approved")

        # Build summary from all logs + draft results
        from outreach_pipeline.graph.agents.pipeline_agent import _format_pipeline_summary
        all_logs = list(state.get("log", []))
        all_logs.append(json.dumps({"type": "drafts", "results": results}))
        summary = _format_pipeline_summary({"log": all_logs})

        from outreach_pipeline.db.replies import update_run
        from outreach_pipeline.db.pool import now as _now
        await update_run(run_id,
                         contacts_fetched=len(state.get("contact_ids", [])),
                         emails_drafted=approved_count,
                         summary=summary,
                         completed_at=_now())

    return {
        "draft_results": results,
        "log": [json.dumps({"type": "drafts", "results": results})],
    }


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

_pipeline_builder = StateGraph(PipelineState)
_pipeline_builder.add_node("ingest", ingest_node)
_pipeline_builder.add_node("contact_worker", contact_worker)
_pipeline_builder.add_node("draft_queue", draft_queue)

_pipeline_builder.add_edge(START, "ingest")
_pipeline_builder.add_conditional_edges(
    "ingest", fan_out_contacts, ["contact_worker", "draft_queue"]
)
_pipeline_builder.add_edge("contact_worker", "draft_queue")
_pipeline_builder.add_edge("draft_queue", END)

pipeline_graph = _pipeline_builder.compile()


# ---------------------------------------------------------------------------
# Draft-only graph (skips ingest/enrich/score)
# ---------------------------------------------------------------------------

async def _draft_only_ingest(state: PipelineState, config: RunnableConfig) -> dict:
    """Load already-scored engagements ready for drafting. No HubSpot call."""
    start = state["start_date"]
    end = state.get("end_date", start)

    from outreach_pipeline.tools.contacts import get_leads
    leads = await get_leads(after=start, before=end)
    contact_ids = [str(l["id"]) for l in leads.get("leads", [])]

    # Find engagements in drafting status
    from outreach_pipeline.tools.engagements import get_engagements
    ready = []
    for cid in contact_ids:
        engs = await get_engagements(contact_id=cid)
        for eng in engs.get("engagements", []):
            if eng.get("score", 0) >= 40 and eng.get("status") == "drafting":
                ready.append(eng["id"])

    # Record run
    from outreach_pipeline.db.replies import start_run as _start_run
    run_id = await _start_run(start, end)

    return {
        "contact_ids": contact_ids,
        "ready_to_draft": ready,
        "run_id": run_id,
        "log": [json.dumps({
            "type": "ingest",
            "fetched": 0,
            "to_process": len(contact_ids),
            "date_range": f"{start} to {end}" if start != end else start,
        })],
    }


_draft_only_builder = StateGraph(PipelineState)
_draft_only_builder.add_node("load", _draft_only_ingest)
_draft_only_builder.add_node("draft_queue", draft_queue)
_draft_only_builder.add_edge(START, "load")
_draft_only_builder.add_edge("load", "draft_queue")
_draft_only_builder.add_edge("draft_queue", END)

draft_only_graph = _draft_only_builder.compile()
