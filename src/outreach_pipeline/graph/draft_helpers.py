"""Shared async helpers for draft writing, validation, and approval.

Used by both DraftReviewMiddleware (interactive drafting) and the
pipeline's draft_queue node (batch drafting with HITL).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

MAX_VALIDATION_RETRIES = 3


def _page_label_from_url(url: str) -> str:
    """Derive a human-readable interest tag from a submission page URL.

    Example: https://www.example.com/products/outdoor-radio/point-to-point-family
             → "Outdoor Radio — Point To Point Family"

    Returns empty string if the URL can't be parsed or has no useful
    path. Used by scoring and drafting when the contact filled a
    lead-capture form that has no free-text message.
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        parts = [seg for seg in p.path.split("/") if seg]
        # Drop generic prefixes like products/ or resources/ so the
        # label focuses on what they actually engaged with.
        if parts and parts[0] in ("products", "resources", "solutions", "blog"):
            parts = parts[1:]
        if not parts:
            return p.netloc or ""
        pretty = " — ".join(
            seg.replace("-", " ").replace("_", " ").title() for seg in parts[:3]
        )
        return pretty
    except Exception:
        return ""


async def _atranslate_if_needed(text: str) -> str:
    """Translate non-English text to English for display in Draft Review cards."""
    if not text or len(text.strip()) < 20:
        return text
    try:
        from langdetect import detect
        lang = detect(text)
        if lang == "en":
            return text
    except Exception:
        return text
    try:
        from outreach_pipeline.graph.agent import _llm, INTERNAL_LLM_CONFIG
        result = await _llm.ainvoke([
            HumanMessage(content=f"Translate to English. Return ONLY the translation, nothing else:\n\n{text}")
        ], config=INTERNAL_LLM_CONFIG)
        translated = result.content.strip()
        return f"{translated}\n\n[Original: {text[:200]}{'...' if len(text) > 200 else ''}]"
    except Exception:
        return text


_DRAFT_RULES = (
    "You are an email drafting assistant. Write a personalized outreach email.\n\n"
    "Rules:\n"
    "- 3-4 sentences.\n"
    "- Start with EXACTLY 'Hi [First Name],' — use the literal placeholder, NEVER a real name.\n"
    "- End with EXACTLY 'Best,\\n[Sender Name]' — use the literal placeholder, NEVER a real name.\n"
    "- Even if the contact's name appears in the message, do NOT use it. Use [First Name].\n"
    "- ONLY reference info the contact explicitly shared. NEVER mention tracking data.\n"
    "- One CTA only, based on score:\n"
    "  Score >= 60: Ask a QUALIFYING QUESTION about their specific project.\n"
    "    Ask about scope, timeline, topology, number of sites, distances, capacity,\n"
    "    or deployment environment. Example: 'Could you share how many links you\n"
    "    need and the distances involved?'\n"
    "    Do NOT offer resources, brochures, or overviews for qualified contacts.\n"
    "  Score 40-59: Offer a SPECIFIC RESOURCE (case study, solution brief, guide).\n"
    "    Example: 'Would you like me to send our mining backhaul solution brief?'\n"
    "- No calls/meetings, no routing language, no credibility claims.\n\n"
    "Return ONLY the email body. No subject line, no commentary."
)


def _load_guidelines_once() -> str:
    """Read email-guidelines.md at module load — it's static sales copy
    that doesn't change at runtime. Reading per-call wastes I/O AND
    blows up prompt caching (the OpenAI cache relies on a stable
    prefix — reading the file per-request is fine content-wise, but
    also guarantees identical bytes for the cache key either way).
    """
    path = (
        Path(__file__).parent.parent.parent.parent
        / ".claude" / "skills" / "outreach-pipeline" / "email-guidelines.md"
    )
    try:
        return path.read_text(encoding="utf-8")[:3000] if path.exists() else ""
    except Exception:
        return ""


_EMAIL_GUIDELINES = _load_guidelines_once()

# Combine rules + guidelines into one static system prompt. Keeping
# guidelines in the SYSTEM message (not the user message) means the
# whole ~2k-token block participates in prompt-prefix caching — every
# draft after the first one is cheaper.
DRAFT_SYSTEM_PROMPT = (
    _DRAFT_RULES
    + (f"\n\nEMAIL GUIDELINES:\n{_EMAIL_GUIDELINES}" if _EMAIL_GUIDELINES else "")
)


# ---------------------------------------------------------------------------
# Async LLM helpers
# ---------------------------------------------------------------------------

async def afix_draft(body: str, violations: list[str]) -> str:
    """Silently re-draft to fix tone violations. Returns original on failure."""
    try:
        from outreach_pipeline.graph.agent import _llm, INTERNAL_LLM_CONFIG

        response = await _llm.ainvoke([
            SystemMessage(content=(
                "Fix the following tone violations in this email. "
                "Return ONLY the corrected email body, no commentary, no subject line. "
                "CRITICAL RULES:\n"
                "- The greeting MUST be exactly 'Hi [First Name],' — replace any real name "
                "(e.g. 'Dear Claudia,') with the literal placeholder [First Name].\n"
                "- The sign-off MUST be exactly 'Best,\\n[Sender Name]' — replace any "
                "other sign-off (Kind regards, etc.) with this exact format.\n"
                "- NEVER use real names. Only [First Name] and [Sender Name] placeholders.\n"
                "- Keep the email 3-4 sentences."
            )),
            HumanMessage(content=f"Violations: {', '.join(violations)}\n\nEmail body:\n{body}"),
        ], config=INTERNAL_LLM_CONFIG)
        return response.content.strip()
    except Exception:
        return body


async def afix_draft_with_feedback(body: str, feedback: str) -> str:
    """Re-draft incorporating the reviewer's feedback. Returns original on failure."""
    try:
        from outreach_pipeline.graph.agent import _llm, INTERNAL_LLM_CONFIG

        response = await _llm.ainvoke([
            SystemMessage(content=(
                "Revise this email based on the reviewer's feedback. "
                "Return ONLY the corrected email body, no commentary, no subject line. "
                "Keep [First Name] and [Sender Name] placeholders. "
                "Keep the email 3-4 sentences."
            )),
            HumanMessage(content=f"Reviewer feedback: {feedback}\n\nCurrent draft:\n{body}"),
        ], config=INTERNAL_LLM_CONFIG)
        return response.content.strip()
    except Exception:
        return body


import re as _re

# Resource-offer patterns: detects when the CTA offers a resource instead of asking
# a qualifying question. Used to enforce score ≥ 70 → qualifying question rule.
_RESOURCE_OFFER_CTA = _re.compile(
    r'\b(?:'
    # Direct send offers
    r'(?:want|like)\s+me\s+to\s+(?:send|share|forward|provide|pull)'
    r'|i\s+(?:can|could|will|\'ll)\s+(?:send|share|forward|provide)\s+(?:you|it|the|a|some|\w+\s+details)'
    r'|(?:can|could)\s+provide\s+(?:pricing|details|information|specs|specifications)'
    r'|(?:shall|should)\s+i\s+send'
    # "I have a [resource]" patterns
    r'|i\s+have\s+(?:a|an|some|the)\s+(?:case\s+study|guide|spec\s+sheet|brief|brochure|resource|white\s*paper|data\s*sheet|overview|documentation|information|details)'
    # "interested in receiving/getting"
    r'|interested\s+in\s+(?:receiving|getting|seeing)\s+(?:these|those|the|our|some)'
    # "interested in a [resource/overview]"
    r'|interested\s+in\s+(?:a|an)\s+(?:detailed|technical|full|comprehensive)?\s*(?:overview|breakdown|summary|demo|walkthrough|presentation)'
    # "would you like/want [resource]"
    r'|(?:would\s+you|do\s+you)\s+(?:like|want)\s+(?:a|an|the|some|more)\s+(?:detailed|technical|full|comprehensive)?\s*(?:overview|info|information|details|breakdown|summary|documentation)'
    r')\b',
    _re.IGNORECASE,
)


async def avalidate_cta(body: str, score: int, contact_message: str = "", product: str = "") -> str:
    """Enforce CTA matches score band. Auto-fixes via LLM if wrong.

    Score ≥ 60: must ask a qualifying question about their project.
    Score 40-59: must offer a specific named resource.
    Returns the (possibly fixed) body.
    """
    if score >= 60 and _RESOURCE_OFFER_CTA.search(body):
        try:
            from outreach_pipeline.graph.agent import _llm, INTERNAL_LLM_CONFIG

            context = body
            if contact_message:
                context += f"\n\n---\nContact's original message: \"{contact_message[:500]}\""
            if product:
                context += f"\nProduct: {product}"

            response = await _llm.ainvoke([
                SystemMessage(content=(
                    "Rewrite this email keeping the greeting, bridge sentence, and sign-off "
                    "exactly the same. ONLY change the CTA (the last sentence before 'Best,').\n\n"
                    "The new CTA must be a qualifying question about the contact's project — "
                    "NOT a resource offer. Read the contact's original message below the ---.\n\n"
                    "Your question MUST reference a specific detail from the contact's message.\n"
                    "Extract a noun or phrase they used and ask a follow-up about it.\n"
                    "Example: if they said '7GHz ATEX' → ask about the ATEX environment.\n"
                    "Example: if they said 'backhaul link' → ask about the distance or capacity.\n"
                    "Example: if they said 'last-mile' → ask about coverage area or subscriber count.\n\n"
                    "BANNED (never use): 'what stage', 'timeline', 'leading the evaluation',\n"
                    "'broader team', 'pricing the main thing', 'links and distances',\n"
                    "'send/share/provide', 'overview/details', 'working through requirements'.\n\n"
                    "Return ONLY the full rewritten email body (Hi [First Name] through [Sender Name])."
                )),
                HumanMessage(content=context),
            ], config=INTERNAL_LLM_CONFIG)

            fixed = response.content.strip()
            # Verify it's a valid email and the CTA (last sentence) changed
            if "Hi [First Name]" in fixed and "[Sender Name]" in fixed:
                return fixed
        except Exception:
            pass
    return body


async def avalidate_with_retry(body: str) -> tuple[str, bool]:
    """Validate body, silently fix up to MAX_VALIDATION_RETRIES times.

    Returns (final_body, passed). The user never sees intermediate failures.
    Passes detailed violation info (label + match) to the fix function so
    the LLM knows exactly what to change.
    """
    from outreach_pipeline.tools.messaging import validate_message

    validation = validate_message(body, profile="cold_outreach")
    attempts = 0

    while not validation.passed and attempts < MAX_VALIDATION_RETRIES:
        # Include the match text so the LLM knows exactly what to fix
        violations = []
        for v in validation.violations:
            label = v["label"]
            match = v.get("match", "")
            if match:
                violations.append(f"{label} (remove/fix: \"{match}\")")
            else:
                violations.append(label)
        body = await afix_draft(body, violations)
        validation = validate_message(body, profile="cold_outreach")
        attempts += 1

    return body, validation.passed


async def awrite_draft(engagement_id: int) -> dict:
    """Write a draft email for an engagement using a direct LLM call.

    Always retrieves the top sendable assets from the resource catalog
    (find_sendable_asset) and includes them in the drafting prompt so the
    LLM can reference real, verifiable resources rather than inventing
    document titles. After the body is composed, extract_promises scans
    for URLs / promise phrases to populate referenced_resource_ids and
    unverified_promises (warning signal to the human reviewer).

    Returns:
        subject, body, rag_context, promised_resources,
        referenced_resource_ids, unverified_promises
        — or empty strings/lists on failure.
    """
    from outreach_pipeline.graph.agent import _llm, INTERNAL_LLM_CONFIG

    # Fetch context
    from outreach_pipeline.tools.workflow import get_drafting_context
    ctx = await get_drafting_context(engagement_id)

    eng = ctx.get("engagement", {})
    contact = ctx.get("contact", {})
    props = contact.get("properties", {})
    subs = ctx.get("submissions", [])
    company_context = contact.get("company_context", "")

    # Email guidelines are baked into DRAFT_SYSTEM_PROMPT at module
    # load — no per-call file read, maximum cache hit on the system
    # prefix.

    # Get RAG matches
    rag_text = ""
    rag_sources = ""
    try:
        from outreach_pipeline.tools.rag import aquery_rag
        product = eng.get("primary_product", "")
        industry = props.get("industry", "")
        query = f"{product} {industry}" if product or industry else "wireless networking"
        results = await aquery_rag(query, collection="sales_materials", top_k=8)
        rag_items = results.get("results", [])
        rag_text = "\n\n".join(r.get("text", "")[:800] for r in rag_items)
        rag_sources = ", ".join(r.get("source", "")[:60] for r in rag_items)
    except Exception:
        pass

    # Get sendable-asset matches from the curated catalog. Unlike RAG
    # (which feeds unstructured context), these are real URLs the
    # drafter may cite and the UI will render as a verified resources
    # panel to the reviewer. Always runs — never conditional.
    promised_resources: list[dict] = []
    try:
        from outreach_pipeline.tools.resource_catalog import find_sendable_asset
        contact_msg = subs[0].get("message", "") if subs else ""
        promised_resources = find_sendable_asset(
            product=eng.get("primary_product", ""),
            industry=props.get("industry", ""),
            keywords=contact_msg[:300],
            top_k=3,
        )
    except Exception:
        pass

    # Build prompt
    contact_msg = subs[0].get("message", "") if subs else ""
    prompt_parts = [
        f"Company: {props.get('company', 'Unknown')}",
        f"Industry: {props.get('industry', '')}",
        f"Score: {eng.get('score', 0)}/100",
        f"Product: {eng.get('primary_product', 'general')}",
    ]
    # Region is stored locally; job title + country are PII-on-demand.
    region = props.get("region", "")
    if region:
        prompt_parts.append(f"Region: {region}")
    try:
        from outreach_pipeline.tools.pii import afetch_contact_pii
        pii = await afetch_contact_pii(
            str(eng.get("contact_id", "")), ["jobtitle", "country"],
        )
        jt = (pii.get("jobtitle") or "").strip()
        country = (pii.get("country") or "").strip()
        if jt and jt != "Unknown":
            prompt_parts.append(f"Job title: {jt}")
        if country:
            prompt_parts.append(f"Country: {country}")
    except Exception:
        pass
    if company_context:
        prompt_parts.append(f"Company context: {company_context[:1500]}")
    if contact_msg:
        prompt_parts.append(f"Contact said: \"{contact_msg[:3000]}\"")
    else:
        # No free-text message (homepage/product-page lead-capture
        # form). Derive an interest signal from the page they were on
        # so the drafter can still personalise.
        pages = []
        for s in subs[:3]:
            url = (s.get("page_url") or "").strip()
            form = (s.get("form_name") or "").strip()
            label = _page_label_from_url(url) or form or url
            if label and label not in pages:
                pages.append(label)
        if pages:
            prompt_parts.append(
                "Contact did NOT leave a message — they submitted a "
                "lead-capture form on: " + "; ".join(pages)
                + ". Personalise based on THAT page's topic; do not "
                "assume they asked a specific question."
            )
    if rag_text:
        prompt_parts.append(f"Relevant materials:\n{rag_text[:4000]}")
    if promised_resources:
        resource_lines = []
        for i, r in enumerate(promised_resources, 1):
            summary = (r.get("summary") or "")[:160]
            resource_lines.append(
                f"  [{i}] {r['title']} ({r['asset_type']})\n"
                f"      URL: {r['url']}\n"
                f"      {summary}"
            )
        prompt_parts.append(
            "Sendable resources (pick AT MOST one to offer — cite by name "
            "exactly, DO NOT invent new document titles):\n"
            + "\n".join(resource_lines)
        )
    context_text = "\n".join(prompt_parts)

    try:
        response = await _llm.ainvoke([
            SystemMessage(content=DRAFT_SYSTEM_PROMPT),
            HumanMessage(content=f"Write an outreach email for this contact.\n\n{context_text}"),
        ], config=INTERNAL_LLM_CONFIG)
        body = response.content.strip()

        # Generate subject
        subj_response = await _llm.ainvoke([
            SystemMessage(content="Write a short email subject line (under 60 chars) for this outreach email. Return ONLY the subject line, nothing else."),
            HumanMessage(content=body),
        ], config=INTERNAL_LLM_CONFIG)
        subject = subj_response.content.strip().strip('"').strip("'")

        # Post-draft promise extraction — never fail the draft on this.
        referenced_ids: list[str] = []
        unverified_promises: list[str] = []
        try:
            from outreach_pipeline.tools.resource_catalog import extract_promises
            referenced_ids, unverified_promises = extract_promises(
                body, promised_resources,
            )
        except Exception:
            pass

        return {
            "subject": subject,
            "body": body,
            "rag_context": rag_sources,
            "promised_resources": promised_resources,
            "referenced_resource_ids": referenced_ids,
            "unverified_promises": unverified_promises,
        }
    except Exception:
        return {
            "subject": "", "body": "", "rag_context": "",
            "promised_resources": [],
            "referenced_resource_ids": [],
            "unverified_promises": [],
        }


async def afetch_draft_context(engagement_id: int) -> dict:
    """Fetch full engagement context for the draft approval card.

    Returns company info, engagement details, submission messages, and enrichment.
    """
    try:
        from outreach_pipeline.tools.workflow import get_drafting_context
        ctx = await get_drafting_context(engagement_id)
        eng = ctx.get("engagement", {})
        contact = ctx.get("contact", {})
        props = contact.get("properties", {})
        subs = ctx.get("submissions", [])

        # All submission messages (not just the first), translated if needed
        contact_messages = []
        for s in subs:
            msg = (s.get("message") or "").strip()
            if msg:
                msg = await _atranslate_if_needed(msg[:1000])
                contact_messages.append(msg)

        # Pages the contact visited — all submissions for this contact,
        # not just the ones linked to this engagement. Deduped by URL.
        visited_pages: list[dict] = []
        try:
            cid = eng.get("contact_id", "")
            if cid:
                from outreach_pipeline.db.pool import get_pool
                pool = get_pool()
                rows = await pool.fetch(
                    """
                    SELECT page_url, page_title
                    FROM submissions
                    WHERE contact_id = $1 AND page_url != ''
                    ORDER BY mql_date DESC NULLS LAST, id DESC
                    """,
                    str(cid),
                )
                for r in rows:
                    if any(p["url"] == r["page_url"] for p in visited_pages):
                        continue
                    visited_pages.append({
                        "url": r["page_url"],
                        "title": r["page_title"] or "",
                    })
                visited_pages = visited_pages[:5]
        except Exception:
            pass

        return {
            # Company info
            "company": props.get("company", ""),
            "industry": props.get("industry", ""),
            "region": props.get("region", ""),
            "company_context": (contact.get("company_context") or "")[:1500],
            # Engagement details
            "engagement_id": eng.get("id", engagement_id),
            "engagement_status": eng.get("status", ""),
            "engagement_kind": eng.get("kind", ""),
            "score": eng.get("score", 0),
            "score_reasoning": eng.get("score_reasoning", ""),
            "primary_product": eng.get("primary_product", ""),
            "buying_intent": eng.get("buying_intent", ""),
            # Submission details
            "intent_type": subs[0].get("intent_type", "") if subs else "",
            "contact_message": contact_messages[0] if contact_messages else "",
            "all_messages": contact_messages,
            "submission_count": len(subs),
            "visited_pages": visited_pages,
        }
    except Exception:
        return {}


def parse_resume(response) -> dict:
    """Parse the resume value from interrupt — handles dict, JSON string, or plain string."""
    parsed = response
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            parsed = response
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        if parsed.lower() in ("approved", "approve", "yes"):
            return {"approved": True}
        return {"feedback": parsed}
    return {}
