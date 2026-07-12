"""Submission classification — determines intent, product family, support/event flags.

This is rule-based classification using the deployment's organization profile
plus generic intent signals. The LLM scorer can refine or override later.
"""
from __future__ import annotations

import re

from outreach_pipeline.organization import (
    get_organization_profile,
    get_product_patterns,
    get_relevance_pattern,
    get_url_product_patterns,
)


# Product vocabulary is deployment data, not application logic. These names
# remain module-level for compatibility with the guardrail module and tests.
_PRODUCT_PATTERNS = get_product_patterns()
_URL_PRODUCT_PATTERNS = get_url_product_patterns()

# --- Support patterns (extracted from db/guardrails.py) ---

_OWNERSHIP = re.compile(
    r'\b(?:'
    r'i\s+have\s+(?:some|a\s+few|\d+)\s'
    r'|i\s+have\s+(?:a\s+)?(?:bad|faulty|defective|broken|dead)\b'
    r'|i\s+have\s+\d+\s+years?\s+(?:of\s+)?using\b'
    r'|i(?:\'ve|\s+have)\s+been\s+(?:using|running|operating)\b'
    r'|we\s+(?:have|own|use)\s+(?:some|a\s+few|\d+)\s'
    r'|(?:i|we)\s+(?:purchased|bought|deployed|installed)\b'
    r'|currently\s+(?:using|running|deployed|installed)\b'
    r')', re.IGNORECASE)

_TROUBLESHOOT = re.compile(
    r'\b(?:'
    r'can\s*not\s+get\s+(?:\w+\s+){0,3}(?:to\s+)?(?:work|connect|link|sync)\b'
    r"|can't\s+get\s+(?:\w+\s+){0,3}(?:to\s+)?(?:work|connect|link|sync)\b"
    r'|(?:tried|trying)\s+different\b'
    r'|reboot(?:s|ing|ed)?\s+randomly\b'
    r'|randomly\s+reboot(?:s|ing|ed)?\b'
    r'|not\s+(?:working|connecting|syncing|linking|functioning)\b'
    r'|(?:equipment|unit|device|radio|link|injector)\s+(?:is\s+)?(?:down|faulty|broken|offline|bad|dead|defective)\b'
    r'|(?:bad|faulty|defective|broken|dead)\s+(?:equipment|unit|device|radio|link|injector|poe)\b'
    r')', re.IGNORECASE)

_SW_UPDATE = re.compile(
    r'\b(?:'
    r'update\s+(?:my|our|all\s+(?:my|our))\s+\w+\s+(?:sw|software|firmware|versions?)\b'
    r'|update\s+(?:sw|firmware|software)\s+(?:versions?|build)\b'
    r'|(?:sw|firmware|software)\s+(?:upgrade|update)\b'
    r'|recommended\s+to\s+update\b'
    r')', re.IGNORECASE)

_STRONG_SUPPORT = re.compile(
    r'\b(?:'
    r'technical\s+support\b'
    r'|contact\s+(?:your\s+|our\s+)?support\b'
    r'|(?:inop|non-?functional)\b'
    r'|(?:warranty\s+(?:claim|replacement|repair)|rma)\b'
    r'|processor\s+reset\b'
    r'|(?:i|we)\s+have\s+(?:a\s+)?(?:bad|faulty|defective|broken|dead)\s+(?:\w+\s+){0,2}(?:unit|device|radio|link|injector|antenna|modem|router|switch|cable|board|module|poe)\b'
    r'|(?:my|our)\s+(?:\w+\s+){0,2}(?:unit|device|radio|link|injector|antenna|modem|router)\s+(?:is\s+)?(?:bad|faulty|defective|broken|dead|down|not\s+working)\b'
    r'|\u062a\u0635\u0641\u064a\u0631\b'
    # Purchase order / order status inquiries
    # Note: PII redaction may replace PO numbers with [phone redacted],
    # so we match PO/P.O. with or without trailing digits.
    r'|(?:status\s+of\s+)?(?:PO|purchase\s+order|P\.O\.)\s*#?\s*(?:\d|\[)'
    r'|order\s+status\b'
    r'|(?:where|when)\s+is\s+(?:my|our|the)\s+(?:order|shipment|delivery)\b'
    r'|tracking\s+(?:number|info|information)\s+for\s+(?:my|our|the)\s+order\b'
    r'|(?:needs?\s+to\s+know\s+about\s+(?:this|the)\s+order)\b'
    r')', re.IGNORECASE)

# --- Events patterns (extracted from db/guardrails.py) ---

# --- Off-topic detection (inquiries outside configured offerings) ---

_PROFILE_RELEVANCE = get_relevance_pattern()

_BUYING_LANGUAGE = re.compile(
    r'\b(?:'
    r'pricing|price|quote|availability|'
    r'purchase|buy|order|procurement|'
    r'comprar|compra|compro|comprando|'
    r'quiero\s+comprar|quisiera\s+comprar|quero\s+comprar|'
    r'precio|precios|preço|preços|cotizaci[oó]n|or[çc]amento|presupuesto|'
    r'(?:best|your)\s+(?:pricing|price|offer|quote)|'
    r'interested\s+in\s+buying'
    r')\b', re.IGNORECASE)

# Positive evidence for categories outside the bundled sample profile. This
# conservative negative heuristic is intentionally separate from the profile's
# configurable positive vocabulary. Deployments whose catalog includes one of
# these categories should adjust or replace this policy before enabling it.
# Without positive excluded-category evidence, an ambiguous pricing inquiry
# flows through normal scoring instead of being pre-emptively blocked.
_DEFAULT_EXCLUDED_CATEGORY = re.compile(
    r'\b(?:'
    # IT/software that isn't ours
    r'printers?|toner|cartridges?|ribbons?|YMCKO|laptops?|desktops?|monitors?|'
    r'keyboards?|photocopiers?|scanners?|projectors?|furniture|office\s+chairs?|'
    r'SaaS|CRM|ERP|HRIS|accounting\s+software|payroll\s+software|'
    # Professional services
    r'legal\s+services?|law\s+firm|attorneys?|'
    r'accounting\s+services?|bookkeeping|tax\s+(?:preparation|filing)|'
    r'medical\s+(?:supplies|devices|equipment)|pharmaceuticals?|'
    r'translation\s+services?|staffing|recruiting\s+services?|'
    r'cleaning\s+services?|janitorial|landscaping|catering|'
    # Physical goods unrelated to networking
    r'vehicles?|automotive|trucks?|cars?|'
    r'uniforms?|apparel|garments|textiles?|'
    r'food|beverages?|groceries|'
    r'construction\s+materials?|cement|steel|lumber|'
    # Marketing/media (not our business)
    r'advertising\s+services?|SEO|digital\s+marketing|'
    r'content\s+writing|copywriting|graphic\s+design'
    r')\b', re.IGNORECASE)

# --- Vendor/supplier pitch detection ---
# Catches inbound messages from companies trying to sell TO us, not buy FROM us.
# These are partnership/supplier pitches, not customer inquiries.

# Strong signals: the contact describes what THEY provide/offer/manufacture
_VENDOR_PROVIDE = re.compile(
    r'\b(?:'
    r'what\s+(?:we|i)\s+(?:can\s+)?(?:provide|offer|manufacture|supply)\b'
    r'|(?:we|our\s+company)\s+(?:is\s+)?(?:a|an)\s+[\w\s]{3,40}(?:provider|manufacturer|supplier|vendor|distributor)\b'
    r'|(?:we|our\s+company)\s+(?:specialize|specialise)s?\s+in\b'
    r'|(?:we|our\s+company)\s+(?:provide|offer|manufacture|supply|produce|deliver)s?\s'
    r'|our\s+(?:main\s+)?products?\s+(?:include|are|cover)\b'
    r'|(?:we|our)\s+(?:have\s+)?(?:established|built)\s+(?:a\s+)?(?:good\s+)?(?:cooperative|business|working)\s+relationship\b'
    r')', re.IGNORECASE)

# Partnership language: explicit cooperation/partnership pitch
_VENDOR_PARTNERSHIP = re.compile(
    r'\b(?:'
    r'prosper\s+together\b'
    r'|(?:mutual|win-win|strategic)\s+(?:cooperation|partnership|collaboration)\b'
    r'|(?:become|be)\s+(?:your|a)\s+(?:partner|supplier|vendor|distributor)\b'
    r'|(?:cooperation|partnership)\s+(?:opportunity|proposal|inquiry)\b'
    r'|(?:explore|discuss)\s+(?:potential\s+)?(?:cooperation|partnership|collaboration)\b'
    r'|interested\s+in\s+(?:becoming|being)\s+(?:your|a)\s+(?:partner|supplier)\b'
    r'|(?:OEM|ODM|white\s+label|private\s+label)\s+(?:partner|service|manufacturing)\b'
    r')', re.IGNORECASE)

# Company self-promotion: extensive intro about their own achievements/coverage
_VENDOR_SELF_PROMO = re.compile(
    r'\b(?:'
    r'our\s+(?:marketing|service|sales)\s+(?:and\s+\w+\s+)?network\s+covers\b'
    r'|our\s+products?\s+(?:are\s+)?distributed\s+in\b'
    r'|(?:we\s+have\s+)?(?:overseas|international)\s+branches?\s+(?:established\s+)?in\b'
    r'|listed\s+on\s+(?:the\s+)?(?:SSE|NYSE|NASDAQ|stock\s+exchange)\b'
    r'|participated\s+in\s+formulating\s+(?:over\s+)?\d+\s+(?:national|international|industry)\b'
    r')', re.IGNORECASE)


def _translate_if_needed(text: str) -> str:
    """Translate non-English text to English for classification.
    Returns the original text if already English — the caller can
    then skip persisting an identity translation."""
    translated = translate_to_english(text)
    return translated if translated else text


def translate_to_english(text: str) -> str:
    """Public helper — returns an English translation of `text`, or
    an empty string if the text is already English / detection
    failed / the LLM was unreachable. Callers that want
    fail-through-to-original should use `_translate_if_needed`.
    """
    if not text or len(text.strip()) < 20:
        return ""
    try:
        from langdetect import detect
        lang = detect(text)
        if lang == "en":
            return ""
    except Exception:
        return ""
    try:
        from outreach_pipeline.graph.agent import _llm
        from langchain_core.messages import HumanMessage
        result = _llm.invoke([HumanMessage(
            content=f"Translate to English. Return ONLY the translation, nothing else:\n\n{text}"
        )])
        out = (result.content or "").strip()
        # Guard against the LLM echoing the input verbatim (happens
        # when it misidentifies the language).
        return out if out and out.lower() != text.strip().lower() else ""
    except Exception:
        return ""

_EVENT_URL = re.compile(r'/events\b', re.IGNORECASE)

_S = r'(?:mwc|mobile\s+world\s+congress|gitex|ibc|ces|gsma|fyuz|ims)'
_MESSAGE_EVENTS = re.compile(
    r'(?:'
    r'meet(?:ing)?\s+(?:you\s+|us\s+|while\s+(?:we\s+are\s+)?)?at\s+' + _S + r'|'
    r'at\s+(?:the\s+)?' + _S + r'\s+(?:booth|event|stand|meeting|show)|'
    + _S + r'\s+(?:booth|meeting|tickets?|stand)|'
    r'tickets?\s+for\s+' + _S + r'|'
    r'visit\s+(?:your|the)\s+booth|'
    r'attend(?:ed|ing)?\s+(?:the\s+|a\s+|an\s+)?(?:' + _S + r'|event|conference|show|webinar|seminar|workshop|summit)'
    r')', re.IGNORECASE)


_LLM_INTENT_LABELS = {
    "sales_inquiry",
    "support_request",
    "vendor_pitch",
    "off_topic",
    "event_meeting",
}


def _has_clear_buyer_intent(message: str, page_url: str = "") -> bool:
    """Return true for explicit buying language tied to our product space."""
    if not message or not message.strip():
        return False
    has_buying_language = bool(_BUYING_LANGUAGE.search(message))
    has_profile_relevance = bool(_PROFILE_RELEVANCE.search(message))
    has_product = any(p.search(message) for p in _PRODUCT_PATTERNS.values())
    has_product_url = any(p.search(page_url or "") for p in _URL_PRODUCT_PATTERNS.values())
    return has_buying_language and (has_profile_relevance or has_product or has_product_url)


def _llm_classify_intent(message: str, form_name: str = "", page_url: str = "") -> str | None:
    """LLM tie-breaker for ambiguous submissions. Returns one of the
    known intent labels or None on failure / unparseable output.

    Used only when the regex is about to default to sales_inquiry but
    the signal profile is ambiguous (zero regex fires, or partial
    non-sales evidence — see classify_intent). The regex decision
    stands whenever this returns None, so LLM outages degrade
    gracefully to prior behaviour.
    """
    if not message or not message.strip():
        return None
    try:
        from outreach_pipeline.graph.agent import _llm
        from langchain_core.messages import HumanMessage
        profile = get_organization_profile()
        prompt = (
            f"Classify this inbound message to {profile.name} ({profile.description}). "
            "Reply with EXACTLY one of:\n"
            "  sales_inquiry   — prospect wants to buy from us (pricing, quote, eval)\n"
            "  support_request — existing customer needs help (firmware, RMA, "
            "config, order status, troubleshooting)\n"
            "  vendor_pitch    — someone selling TO us (partnership/supplier pitch)\n"
            "  off_topic       — inquiry clearly outside the configured offerings\n"
            "  event_meeting   — wants to meet at an event (MWC/GITEX/etc)\n\n"
            f"Form: {form_name!r}\n"
            f"Page:  {page_url!r}\n"
            f"Message:\n{message}\n\n"
            "Reply with JUST the label, nothing else."
        )
        result = _llm.invoke([HumanMessage(content=prompt)])
        out = (result.content or "").strip().lower().strip(".,!?'\"")
        return out if out in _LLM_INTENT_LABELS else None
    except Exception:
        return None


def classify_intent(message: str, form_name: str = "", page_url: str = "", topic: str = "") -> dict:
    """Classify a submission's intent type and flags.

    Args:
        topic: Self-declared topic from the Contact Us form dropdown.
            Values: Support, Sales Inquiry, Careers, Investor relations, Partnerships, Other.
            This is the most authoritative signal — the contact chose it explicitly.

    Returns dict with: intent_type, support_flag, event_flag.
    """
    # HubSpot-declared topic wins for the clearly non-sales categories.
    # The contact explicitly picked this from a dropdown, so it's a
    # stronger signal than anything we can infer from the free-text
    # message. In particular, a supplier can select `Partnerships` and
    # describe materials or services without using the phrases covered by
    # the vendor-pitch regex. Trust the dropdown unless the message contains
    # explicit buyer language for a configured product.
    topic_value = (topic or "").strip()
    if topic_value == "Partnerships" and not _has_clear_buyer_intent(message, page_url):
        return {
            "intent_type": "vendor_pitch",
            "support_flag": False,
            "event_flag": False,
        }

    _TRUSTED_TOPIC = {
        "Support": "support_request",
        "Careers": "off_topic",
        "Investor relations": "off_topic",
    }
    trusted = _TRUSTED_TOPIC.get(topic_value)
    if trusted is not None:
        return {
            "intent_type": trusted,
            "support_flag": trusted == "support_request",
            "event_flag": False,
        }

    support_flag = False
    event_flag = False
    english_msg = message
    has_company_product = False

    # Check support
    has_ownership = bool(_OWNERSHIP.search(message))
    has_troubleshoot = bool(_TROUBLESHOOT.search(message))
    has_sw_update = bool(_SW_UPDATE.search(message))
    standalone_support = bool(_STRONG_SUPPORT.search(message))
    if standalone_support or (has_ownership and (has_troubleshoot or has_sw_update)):
        support_flag = True

    # Check events
    if _EVENT_URL.search(page_url) or _MESSAGE_EVENTS.search(message):
        event_flag = True

    # Translate and check product patterns (shared by vendor pitch + off-topic)
    if message and message.strip():
        english_msg = _translate_if_needed(message)
        has_company_product = any(p.search(english_msg) for p in _PRODUCT_PATTERNS.values())
        if not has_company_product:
            has_company_product = any(p.search(message) for p in _PRODUCT_PATTERNS.values())

    # Check vendor pitch: someone selling TO us, not buying FROM us.
    vendor_pitch = False
    if message and message.strip():
        has_vendor_provide = bool(_VENDOR_PROVIDE.search(english_msg))
        has_partnership = bool(_VENDOR_PARTNERSHIP.search(english_msg))
        has_self_promo = bool(_VENDOR_SELF_PROMO.search(english_msg))
        if not has_company_product and (
            has_vendor_provide or (has_partnership and has_self_promo)
        ):
            vendor_pitch = True

    # Check off-topic: positive evidence that the inquiry is about a category
    # excluded by the bundled sample policy. Missing profile keywords alone is
    # not enough; the scorer can still assign product_fit=0. This prevents
    # ambiguous partnership inquiries from being pre-emptively blocked.
    off_topic = False
    if message and message.strip() and not vendor_pitch:
        has_profile_relevance = bool(_PROFILE_RELEVANCE.search(english_msg))
        has_excluded_category = bool(_DEFAULT_EXCLUDED_CATEGORY.search(english_msg))
        if has_excluded_category and not has_profile_relevance and not has_company_product:
            off_topic = True

    # Determine intent type
    if not message or not message.strip():
        # Empty message: check form_name to distinguish contact-us from content downloads
        if form_name and re.search(r'contact\s*us', form_name, re.IGNORECASE):
            intent_type = "sales_inquiry"
        else:
            intent_type = "content_download"
    elif support_flag:
        intent_type = "support_request"
    elif event_flag:
        intent_type = "event_meeting"
    elif vendor_pitch:
        intent_type = "vendor_pitch"
    elif off_topic:
        intent_type = "off_topic"
    else:
        intent_type = "sales_inquiry"

    # LLM tie-breaker: the regex is about to default to sales_inquiry,
    # but the signal profile looks ambiguous. Two shapes are treated
    # as ambiguous and handed to the LLM:
    #   1. ZERO-FIRE: no intent-relevant regex group matched at all, so
    #      the sales_inquiry label has no supporting rule signal.
    #   2. PARTIAL NON-SALES: a support/vendor/off_topic subsignal
    #      fired but didn't meet the full AND rule. For example, "currently
    #      running firmware, would like the latest version" can fire
    #      _OWNERSHIP without _TROUBLESHOOT or _SW_UPDATE.
    # When the LLM is unreachable or returns garbage, _llm_classify_intent
    # returns None and the regex decision stands — no behavioural
    # regression from prior to this hook.
    if intent_type == "sales_inquiry" and message and message.strip():
        support_partial = (
            (has_ownership or has_troubleshoot or has_sw_update)
            and not support_flag
        )
        vendor_partial = (
            (has_vendor_provide or has_partnership or has_self_promo)
            and not vendor_pitch
        )
        offtopic_partial = has_excluded_category and not off_topic
        any_regex_signal = any([
            has_ownership, has_troubleshoot, has_sw_update, standalone_support,
            event_flag, has_company_product,
            has_vendor_provide, has_partnership, has_self_promo,
            has_profile_relevance, has_excluded_category,
        ])
        ambiguous = (
            support_partial or vendor_partial or offtopic_partial
            or not any_regex_signal
        )
        if ambiguous:
            llm_label = _llm_classify_intent(message, form_name, page_url)
            if llm_label:
                intent_type = llm_label
                support_flag = llm_label == "support_request"
                event_flag = llm_label == "event_meeting"

    return {
        "intent_type": intent_type,
        "support_flag": support_flag,
        "event_flag": event_flag,
    }


def classify_product_family(message: str, page_url: str = "") -> str:
    """Determine which product family a submission relates to.

    Returns a product_family enum value.
    """
    if not message and not page_url:
        return "other"

    # Check message first
    for family, pattern in _PRODUCT_PATTERNS.items():
        if pattern.search(message):
            return family

    # Check page URL
    for family, pattern in _URL_PRODUCT_PATTERNS.items():
        if pattern.search(page_url):
            return family

    if message.strip():
        return "general"
    return "other"
