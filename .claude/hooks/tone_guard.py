#!/usr/bin/env python3
"""PreToolUse hook that validates outgoing email content for tone and professionalism.

Intercepts Bash tool calls to draft-email and send-email, checks the --body
and --subject arguments for unprofessional language, aggressive tone, or
inappropriate content. Blocks the tool call if violations are found.

Exit codes:
  0 — content is clean, proceed
  2 — content blocked (violation found, message returned to Claude)
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG = Path("data/tone_audit.log")

# --- Blocked patterns ---

PROFANITY = re.compile(
    r'\b(?:damn|hell|crap|shit|fuck|ass|bastard|bitch|dick|piss|'
    r'screw\s+you|shut\s+up|idiot|stupid|moron|dumb|loser|suck[s]?)\b',
    re.IGNORECASE,
)

AGGRESSIVE_TONE = re.compile(
    r'\b(?:you\s+must|you\s+need\s+to|you\s+have\s+to|you\s+should\s+have|'
    r'don\'?t\s+waste|stop\s+wasting|last\s+chance|final\s+warning|'
    r'act\s+now\s+or|you\'?re\s+missing\s+out|you\'?re\s+making\s+a\s+mistake|'
    r'don\'?t\s+be\s+(?:stupid|foolish|ignorant)|what\'?s\s+wrong\s+with\s+you|'
    r'you\s+clearly\s+don\'?t|I\s+(?:demand|insist))\b',
    re.IGNORECASE,
)

MANIPULATIVE = re.compile(
    r'\b(?:limited\s+time\s+(?:only|offer)|act\s+(?:fast|immediately|now)|'
    r'once\s+in\s+a\s+lifetime|exclusive\s+(?:deal|offer)\s+just\s+for\s+you|'
    r'you\'?d\s+be\s+(?:crazy|insane)\s+(?:not\s+to|to\s+miss)|'
    r'your\s+(?:competitors|rivals)\s+are\s+already|'
    r'guaranteed\s+(?:results|ROI|success)|risk[\s-]free)\b',
    re.IGNORECASE,
)

DISCRIMINATORY = re.compile(
    r'\b(?:(?:old|young)\s+(?:people|guys|folks)|'
    r'you\s+(?:people|guys|types)|'
    r'(?:typical|expected)\s+(?:for|of)\s+(?:your|a)\s+(?:kind|type|sort))\b',
    re.IGNORECASE,
)

FALSE_CLAIMS = re.compile(
    r'\b(?:100%\s+guaranteed|zero\s+risk|no\s+risk|'
    r'proven\s+to\s+(?:work|succeed)|never\s+fails|'
    r'(?:best|cheapest|fastest)\s+(?:in\s+the\s+(?:world|market|industry)))\b',
    re.IGNORECASE,
)

# Detects references to internal CRM/analytics tracking data that the prospect
# never shared with us — page views, site visit paths, click tracking, etc.
# Referencing this data in outreach emails is a serious privacy/trust violation.
TRACKING_DATA = re.compile(
    r'(?:'
    # Page view counts: "35 page views", "viewed 10 pages"
    r'\d+\s+page\s+views?\b'
    r'|(?:viewed|visited)\s+\d+\s+pages?\b'
    # Specific page entry points: "started on the X page", "landed on", "came in through"
    r'|(?:started|began|landed|came\s+in)\s+(?:on|from|through)\s+(?:the\s+)?["\w\s-]+page\b'
    # Browsing behaviour the prospect didn't disclose
    r'|(?:i\s+(?:can\s+)?see\s+(?:that\s+)?you|i\s+noticed\s+you|i\s+saw\s+(?:that\s+)?you)\s+(?:visited|viewed|clicked|browsed|checked\s+out|were\s+looking|(?:\'ve\s+)?been\s+working)'
    r'|you(?:\'ve)?\s+(?:been\s+)?(?:looking\s+at|viewed|visited|browsed|checked\s+out)\s+(?:our|the)\b'
    # "I saw you" / "I see you" as surveillance — implies monitoring activity
    r'|i\s+(?:saw|see|noticed)\s+(?:that\s+)?you(?:\'(?:ve|re))?\s+(?:have\s+been|been|are|were)\b'
    r'|your\s+(?:recent\s+)?(?:visit|session|browsing|activity|behavior)\s+(?:on|to)\s+(?:our|the)\b'
    r'|based\s+on\s+your\s+(?:visit|browsing|activity|behavior|interest\s+in\s+our)\b'
    # Email/link click tracking
    r'|you\s+clicked\s+(?:the|our|a|an)\b'
    r'|after\s+(?:you\s+)?clicking\s+(?:the|our|a|an)\b'
    # Download tracking — any form of referencing what the prospect downloaded
    r'|you(?:\'ve)?\s+(?:already\s+|recently\s+)?downloaded\b'
    r'|(?:the|our|a|an)\s+\S+(?:\s+\S+){0,4}\s+you\s+downloaded\b'
    r'|after\s+(?:you\s+)?downloading\b'
    r'|i\s+(?:can\s+)?see\s+(?:that\s+)?you\s+downloaded\b'
    # Content download references — implying knowledge of gated content access
    r'|(?:since|as|because)\s+you\s+(?:accessed|grabbed|requested|read)\s+(?:the|our)\s+(?:solution\s+brief|white\s*paper|case\s+study|data\s*sheet|brochure)\b'
    r'|your\s+(?:interest\s+in|download\s+of)\s+(?:the|our)\s+(?:solution\s+brief|white\s*paper|case\s+study|data\s*sheet|brochure)\b'
    r')',
    re.IGNORECASE,
)

ALL_CAPS_WORDS = re.compile(r'\b[A-Z]{4,}\b')

EM_DASH = re.compile(r'\s[—–]\s|\s-{2,}\s')

FILLER_OPENERS = re.compile(
    r'\b(?:i\s+hope\s+(?:this|you)|i\s+(?:wanted|am\s+writing|\'m\s+writing)\s+to\s+(?:reach\s+out|connect|follow|touch)|'
    r'thank\s+you\s+for\s+(?:your\s+interest|reaching\s+out|contacting)|'
    r'i\s+(?:am\s+)?reaching\s+out\s+(?:to|because|regarding)|'
    r'feel\s+free\s+to|don\'?t\s+hesitate\s+to|please\s+let\s+me\s+know\s+if\s+you\s+have\s+any\s+questions|'
    r'happy\s+to\s+(?:walk|help|assist|show|discuss|share|answer|provide|give)|'
    r'good\s+to\s+(?:hear\s+from\s+you|connect|meet)|'
    r'great\s+to\s+(?:hear\s+from\s+you|connect|meet))\b',
    re.IGNORECASE,
)

MARKETING_ADJECTIVES = re.compile(
    r'\b(?:industry[\s-]leading|cutting[\s-]edge|world[\s-]class|best[\s-]in[\s-]class|'
    r'state[\s-]of[\s-]the[\s-]art|innovative\s+solution|game[\s-]changing|'
    r'powerful\s+(?:solution|platform|tool|product)|robust\s+(?:solution|platform)|'
    r'seamless(?:ly)?|next[\s-]gen(?:eration)?|'
    r'future[\s-]proof|cost[\s-]effective|total\s+cost\s+of\s+ownership|'
    r'high[\s-]performance|(?:highly\s+)?scalable|mission[\s-]critical|'
    r'unparalleled|unmatched|superior\s+(?:performance|quality)|'
    r'designed\s+to\s+(?:be\s+)?(?:future[\s-]proof|scalable|reliable))\b',
    re.IGNORECASE,
)

CALL_REQUEST = re.compile(
    r'\b(?:(?:schedule|book|set\s+up|arrange|find|request)\s+(?:a\s+)?(?:call|meeting|time|chat|conversation|demo|sync|walkthrough)|'
    r'hop\s+on\s+a\s+call|jump\s+on\s+a\s+call|get\s+on\s+a\s+call|'
    r'(?:quick\s+)?call\s+(?:with\s+you|to\s+discuss|sometime)|'
    r'(?:30|15|45|60)[\s-]minute\s+(?:call|meeting|chat|sync|demo)|'
    r'time\s+on\s+your\s+calendar|slot\s+on\s+your\s+calendar|'
    r'connect(?:ing)?\s+you\s+with|look\s+into\s+connect(?:ing)?\s+you|'
    r'introduc(?:e|ing)\s+you\s+to\s+(?:our|one\s+of)|'
    r'on\s+our\s+team\b|'
    r'calendly|calendar\s+link|pick\s+a\s+time)\b',
    re.IGNORECASE,
)

_RESOURCE = r'(?:pricing|a\s+quote|numbers|lead\s+times?|delivery(?:\s+times?)?|(?:the\s+|a\s+|our\s+)?(?:full\s+|complete\s+|detailed\s+|custom\s+)?spec(?:s|ification)?(?:\s+sheet)?|a\s+proposal|a\s+comparison)'
FALSE_PROMISE = re.compile(
    r'\b(?:'
    # "putting/put together pricing/specs/quote"
    r'(?:putting|put)\s+together\s+' + _RESOURCE + r'|'
    # "pull/pulling/compile/compiling/prepare/preparing/assemble/assembling together ..."
    r'(?:pull(?:ing)?|compil(?:e|ing)|prepar(?:e|ing)|assembl(?:e|ing))\s+together\s+' + _RESOURCE + r'|'
    # "I can/will/am/'ll/'m pull/pulling/compile/... ..."
    r'(?:i\s+(?:can|will|\'ll|am)\s+|i\'m\s+)(?:pull(?:ing)?|compil(?:e|ing)|prepar(?:e|ing)|assembl(?:e|ing)|get(?:ting)?)\s+' + _RESOURCE + r'|'
    # Bare verb/gerund without "I" prefix: "pull pricing", "preparing a quote"
    r'(?:pull(?:ing)?|compil(?:e|ing)|prepar(?:e|ing)|assembl(?:e|ing))\s+' + _RESOURCE + r'|'
    # "getting you pricing/a quote/lead times"
    r'get(?:ting)?\s+you\s+' + _RESOURCE + r'|'
    # "working on the pricing/specs/quote"
    r'working\s+on\s+(?:the\s+)?' + _RESOURCE + r'|'
    # "get you access/set you up/set you up with access" — can't provision portal access
    r'get\s+you\s+(?:set\s+up|access|onboarded|registered|an\s+account)|'
    r'set\s+you\s+up\s+(?:with\s+)?(?:access|an\s+account|in\s+the\s+portal)|'
    # Speculation about prospect's account/access status we have no visibility into
    r'your\s+account\s+(?:may|might|should|could|probably)\s+(?:already|still)|'
    r'you\s+(?:may|might|should|could)\s+(?:already\s+)?(?:have|be\s+in|have\s+access|be\s+registered|be\s+set\s+up)|'
    r'you\s+(?:already|might\s+already|may\s+already)\s+(?:have|be)|'
    r'(?:may|might)\s+(?:just\s+)?need\s+to\s+be\s+(?:migrated|updated|reactivated|transferred)'
    r')\b',
    re.IGNORECASE,
)

TEAM_HANDOFF = re.compile(
    r'\b(?:'
    # "we'll have someone/the team follow up/get back to you"
    r'(?:we\'ll|we\s+will)\s+(?:have\s+)?(?:(?:someone|a\s+colleague)(?:\s+from\s+(?:our|the)\s+team)?|(?:our|the)\s+team)\s+(?:follow\s+up|get\s+back|reach\s+out|be\s+in\s+touch|contact\s+you|respond)|'
    # "someone from our team will..."
    r'(?:someone|a\s+colleague)\s+(?:from\s+(?:our|the)\s+team\s+)?will\s+(?:follow\s+up|get\s+back|reach\s+out|be\s+in\s+touch|contact\s+you|respond)|'
    # "our team will get back to you"
    r'(?:our|the)\s+team\s+will\s+(?:follow\s+up|get\s+back|reach\s+out|be\s+in\s+touch|contact\s+you|respond|review)|'
    # "will get back to you" (standalone)
    r'(?:will|we\'ll)\s+get\s+back\s+to\s+you'
    r')\b',
    re.IGNORECASE,
)

CONFIDENTIAL_ASK = re.compile(
    r'\b(?:'
    # "share/send/provide your/the RFQ/tender/procurement docs"
    r'(?:share|send|provide|forward)\s+(?:your|the)\s+(?:full\s+)?(?:rfq|rfp|tender|procurement|bid)\s*(?:document|docs?)?|'
    # "could you share the RFQ"
    r'could\s+you\s+(?:share|send|provide|forward)\s+(?:the\s+)?(?:full\s+)?(?:rfq|rfp|tender|procurement|bid)'
    r')\b',
    re.IGNORECASE,
)

TECHNICAL_JARGON = re.compile(
    r'\b(?:'
    # RF engineering terms
    r'link\s+(?:budget|distance|loss|margin)|path\s+loss|fade\s+margin|EIRP|'
    r'antenna\s+(?:gain|configurations?)|beam\s*width|VSWR|'
    # Modulation/signal
    r'QPSK|(?:16|64|128|256)[\s-]?QAM|OFDM|modulation\s+scheme|channel\s+spacing|'
    # Architecture internals
    r'hot\s+standby|XPIC|frequency\s+reuse|'
    # Interface/capacity internals
    r'(?:TDM|E1|STM-\d)\s+(?:port|interface|capacity|support)|bit\s+error\s+rate|'
    r'receive\s+sensitivity|transmit\s+power|'
    # Restating product capabilities in technical terms
    r'(?:supports?|covers?)\s+(?:both\s+)?(?:point-to-point|PtP)\s+(?:and|or)\s+(?:point-to-multipoint|PtMP)'
    r')\b',
    re.IGNORECASE,
)

GATEKEEPER = re.compile(
    r'\b(?:once\s+i\s+(?:know|understand|have|get)|'
    r'once\s+(?:i\s+)?(?:know|understand)\s+(?:a\s+bit\s+more|more\s+about)|'
    r'after\s+(?:i\s+)?(?:learn|understand|know)\s+more|'
    # Routing gatekeeping — "route/direct/connect/pass you to the right person/team"
    r'(?:i\s+(?:want|need|can)\s+to\s+)?(?:make\s+sure\s+i\s+)?(?:route|direct|pass|forward|connect)\s+(?:your|this|you|the)\s+(?:request|inquiry|question|message|rfq)?\s*to\s+the\s+right|'
    r'make\s+sure\s+(?:i\s+)?(?:route|get\s+you\s+to|connect\s+you\s+with)\s+the\s+right|'
    # False acknowledgement — "your RFQ has been received", "your request has been forwarded"
    # Use [\s\S]{0,60} to match even when a model-number clause separates "RFQ" from "has been"
    r'(?:your\s+)?(?:rfq|request|inquiry|message|order)[\s\S]{0,60}(?:has\s+been|have\s+been)\s+(?:received|forwarded|passed\s+on|sent\s+to|routed)|'
    r'has\s+been\s+received\b|'
    r'(?:i\'?m|i\s+am)\s+(?:routing|forwarding|passing|sending)\s+(?:it|this|your\s+\w+)\s+to\s+(?:the\s+)?(?:right|our))\b',
    re.IGNORECASE,
)

WEAK_CTA = re.compile(
    r'\b(?:would\s+that\s+be\s+(?:useful|helpful|valuable|of\s+interest|of\s+value)|'
    r'does\s+that\s+(?:sound|seem)\s+(?:helpful|useful|good|interesting|relevant)|'
    r'would\s+(?:that|this)\s+(?:work|interest\s+you|be\s+of\s+interest)|'
    r'is\s+that\s+(?:something\s+)?(?:useful|helpful|of\s+interest)\s+to\s+you|'
    r'let\s+me\s+know\s+(?:if\s+(?:that|this)\s+(?:sounds|works|is\s+helpful)|what\s+you\s+think))\b',
    re.IGNORECASE,
)

# --- PII patterns (mirror sanitize_output.py — block outbound, not just redact inbound) ---

# Full person name (FirstName LastName) in contexts that signal a real person is being referenced.
# Two TitleCase words are too common generally (product names, company names, geography),
# so we only match when the surrounding context makes it clear a person is being named.
_NAME = r'[A-Z][a-z]{1,19}\s+[A-Z][a-z]{1,19}'
_NOT_PRONOUN = r'(?!(?:our|the|your|my|us|you|them|it|all|this|that|his|her|their)\s)'
_person_name_pat = (
    r'(?:'
    # After verbs that introduce/direct a person: "ask Alex Pero", "contact John Smith"
    r'\b(?:ask|contact|email|call|meet|cc\b|copy\b|speak\s+with|talk\s+to|'
    r'reach\s+out\s+to|get\s+back\s+to|follow\s+up\s+with)\s+' + _NOT_PRONOUN + _NAME
    + r'|'
    # Name followed by a generic team/org indicator.
    + _NAME + r'\s+(?:from\s+(?:our\s+)?(?:team|company)|on\s+(?:our\s+)?team|at\s+(?:our|the)\s+company)'
    + r'|'
    # Name followed by will/can acting as the person: "Alex Pero will follow up"
    + _NAME + r'\s+(?:will|can|would|could)\s+(?:follow\s+up|reach\s+out|be\s+in\s+touch|help|assist|answer|call|email)'
    + r')'
)
PII_PERSON_NAME = re.compile(_person_name_pat, re.IGNORECASE)

# Email addresses in the body: the only email that should appear is from the headers
PII_EMAIL = re.compile(r'\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}\b')

# Phone numbers: E.164, US (xxx) xxx-xxxx, international +xx-xxx-xxxx
PII_PHONE = re.compile(r'(?:\+?\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]\d{3}[\s\-]\d{4}\b')

# Deal values / pricing figures — currency symbol or unit suffix required
PII_DEAL_VALUE = re.compile(
    r'(?:[\$€£¥])\s*\d[\d,.]*\s*(?:[KkMmBb](?:illion)?)?'
    r'|'
    r'\d[\d,.]*\s*[KkMmBb]?\s*(?:USD|EUR|GBP|ARR|MRR|ACV|TCV)',
    re.IGNORECASE,
)


CHECKS = [
    ("PROFANITY", PROFANITY, "Contains profanity or vulgar language"),
    ("AGGRESSIVE", AGGRESSIVE_TONE, "Uses aggressive or pressuring tone"),
    ("MANIPULATIVE", MANIPULATIVE, "Contains manipulative sales tactics"),
    ("DISCRIMINATORY", DISCRIMINATORY, "Contains potentially discriminatory language"),
    ("FALSE_CLAIMS", FALSE_CLAIMS, "Makes unverifiable absolute claims"),
    ("TRACKING_DATA", TRACKING_DATA,
     "Exposes internal tracking data (page views, visit paths, click events) the prospect never shared — remove all references to their browsing behaviour"),
    ("CALL_REQUEST", CALL_REQUEST,
     "Asks for a call, meeting, or offers to connect with someone — use a qualifying question or a specific resource offer instead"),
    ("EM_DASH", EM_DASH,
     "Contains em dash used as a sentence break — rewrite as two separate sentences instead"),
    ("FALSE_PROMISE", FALSE_PROMISE,
     "Promises to personally compile pricing, specs, or proposals you don't have — offer portal access or ask a qualifying question instead"),
    ("TEAM_HANDOFF", TEAM_HANDOFF,
     "Promises someone from the team will follow up or get back to them — you can't guarantee what other people will do"),
    ("CONFIDENTIAL_ASK", CONFIDENTIAL_ASK,
     "Asks the prospect to share their RFQ/RFP/tender documents — these are typically confidential"),
    ("TECHNICAL_JARGON", TECHNICAL_JARGON,
     "Too technical — stay high-level like a project manager, not an RF engineer or integrator"),
    ("WEAK_CTA", WEAK_CTA,
     "Permission-seeking CTA ('would that be useful?', 'does that sound helpful?') — be direct, assume relevance, ask 'want me to send it?' not 'would that help?'"),
    ("GATEKEEPER", GATEKEEPER,
     "Conditional gatekeeping ('once I know a bit more', 'once I understand') — don't make the prospect jump through a hoop before helping them"),
    ("FILLER", FILLER_OPENERS,
     "Contains filler phrase — delete it and get straight to the point"),
    ("OVERSELLING", MARKETING_ADJECTIVES,
     "Contains marketing adjective — remove it, let the product speak for itself"),
    ("PII_PERSON_NAME", PII_PERSON_NAME,
     "Person's full name in body — remove it; only [First Name] and [Sender Name] placeholders are allowed. Never name internal employees or third parties."),
    ("PII_EMAIL", PII_EMAIL,
     "Email address in body — remove it; the recipient address belongs in headers only, not in the message body"),
    ("PII_PHONE", PII_PHONE,
     "Phone number in body — remove it; contact details must not appear in outbound email content"),
    ("PII_DEAL_VALUE", PII_DEAL_VALUE,
     "Deal value or pricing figure in body — remove it; never include specific monetary amounts in outreach emails"),
]


_UNCONDITIONAL_SEND = re.compile(
    r'\b(?:i\s+can\s+send|i\'ll\s+send|i\s+will\s+send|i\s+could\s+send|'
    r'happy\s+to\s+send|send\s+(?:it|that|them|the\s+\w+)\s+over|'
    r'i\s+can\s+(?:share|forward|provide)\s+(?:the|a|that)|'
    # implicit resource mentions: "we have a case study", "I have a resource", "there is a report"
    r'(?:we|i)\s+(?:also\s+)?have\s+(?:a|an|the|some)\s+\w+(?:\s+\w+){0,4}\s+(?:that|which)\s+(?:covers|maps|addresses|shows|details)|'
    r'(?:we|i)\s+(?:also\s+)?have\s+(?:material|a\s+case\s+study|a\s+resource|a\s+report|an\s+overview|a\s+guide|a\s+write[\s-]up))\b',
    re.IGNORECASE,
)


MISSING_GREETING = re.compile(r'^Hi\s+\[First Name\]', re.IGNORECASE)
MISSING_SIGNOFF  = re.compile(r'Best,\s*\n\s*\[Sender Name\]', re.IGNORECASE)

# Non-Latin scripts: Arabic, CJK, Japanese, Korean, Cyrillic, Hebrew
_NON_LATIN_SCRIPT = re.compile(
    r'[\u0600-\u06FF\u0750-\u077F'   # Arabic
    r'\u4E00-\u9FFF\u3040-\u30FF'    # CJK / Hiragana / Katakana
    r'\uAC00-\uD7AF'                 # Korean
    r'\u0400-\u04FF'                 # Cyrillic
    r'\u0590-\u05FF]'                # Hebrew
)
# Accented Latin: é ñ ü etc. — high ratio indicates Spanish/French/Portuguese body
_ACCENTED_LATIN = re.compile(r'[àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞŸ]')
# Distinctive non-English words that essentially never appear in English business emails
_NON_ENGLISH_WORDS = re.compile(
    r'\b(?:'
    r'gracias|estimado[as]?|saludos|encantados?|nuestros|por\s+favor|muchas\s+gracias|'  # Spanish
    r'bonjour|merci|cordialement|veuillez|'                                               # French
    r'sehr\s+geehrte[rns]?|vielen\s+dank|mit\s+freundlichen|'                            # German
    r'obrigado[a]?|prezado[a]?|atenciosamente'                                            # Portuguese
    r')\b',
    re.IGNORECASE,
)


def check_tone(content, body=None):
    """Check text for tone violations. Returns list of {label, reason, match}.

    Args:
        content: Combined subject + body text for general content checks.
        body: Body-only text for structural checks (greeting, sign-off).
              If None, falls back to content for structural checks.
    """
    text = content
    body_text = body if body is not None else content

    violations = []
    for label, pattern, reason in CHECKS:
        for match in pattern.finditer(text):
            violations.append({"label": label, "reason": reason, "match": match.group()})

    # Check for excessive ALL CAPS (more than 2 all-caps words = shouting)
    caps = ALL_CAPS_WORDS.findall(text)
    # Filter out common acronyms
    caps = [w for w in caps if w not in ("HIPAA", "GDPR", "CCPA", "SOC2", "SLA", "API", "CRM", "ERP", "SAP", "ROI", "SaaS", "HTTP", "HTML", "JSON", "SMTP")]
    if len(caps) > 2:
        violations.append({"label": "SHOUTING", "reason": "Excessive ALL CAPS — reads as shouting", "match": ", ".join(caps[:5])})

    # Language check — all outreach emails must be in English
    m = _NON_LATIN_SCRIPT.search(body_text)
    if m:
        violations.append({
            "label": "NON_ENGLISH",
            "reason": "Email body contains non-Latin script — all outreach must be written in English",
            "match": m.group(),
        })
    else:
        # Word-level detection: distinctive non-English function words
        m2 = _NON_ENGLISH_WORDS.search(body_text)
        if m2:
            violations.append({
                "label": "NON_ENGLISH",
                "reason": "Email body appears to be non-English — all outreach must be written in English",
                "match": m2.group(),
            })
        else:
            # Fallback: high ratio of accented Latin characters
            accented = _ACCENTED_LATIN.findall(body_text)
            alpha = re.findall(r'[a-zA-Z]', body_text)
            if len(alpha) > 30 and len(accented) / (len(alpha) + len(accented)) > 0.06:
                violations.append({
                    "label": "NON_ENGLISH",
                    "reason": (
                        f"Email body appears to be non-English "
                        f"({len(accented)} accented characters, "
                        f"{len(accented) * 100 // (len(alpha) + len(accented))}% of text) "
                        f"— all outreach must be written in English"
                    ),
                    "match": accented[0],
                })

    # Check greeting and sign-off templates are present in the body only
    if not MISSING_GREETING.search(body_text.lstrip()):
        violations.append({"label": "MISSING_GREETING", "reason": "Email must start with 'Hi [First Name],'", "match": body_text[:30].strip()})
    if not MISSING_SIGNOFF.search(body_text):
        violations.append({"label": "MISSING_SIGNOFF", "reason": "Email must end with 'Best,' followed by '[Sender Name]'", "match": ""})

    # Check for combined CTA: an unconditional send offer + a separate question.
    # A well-formed email has exactly one CTA. If there's both a send offer stated
    # as a fact ("I can send it over") and a separate question mark, that's two CTAs.
    if _UNCONDITIONAL_SEND.search(text):
        # Count question marks not part of a "want me to send...?" offer
        # Strip want-me-to-send questions first, then check for remaining ?
        stripped = re.sub(r'want\s+me\s+to\s+send[^?]*\?', '', text, flags=re.IGNORECASE)
        stripped = re.sub(r'shall\s+i\s+send[^?]*\?', '', stripped, flags=re.IGNORECASE)
        if stripped.count('?') >= 1:
            violations.append({
                "label": "COMBINED_CTA",
                "reason": "Contains both a send offer and a separate question — pick one CTA only",
                "match": _UNCONDITIONAL_SEND.search(text).group(),
            })

    return violations


def extract_flag_value(cmd, flag):
    """Extract the value of a quoted CLI flag from a command string."""
    idx = cmd.find(flag)
    if idx < 0:
        return ""
    rest = cmd[idx + len(flag):].strip()
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end > 0 else rest[1:]
    elif rest.startswith("'"):
        end = rest.find("'", 1)
        return rest[1:end] if end > 0 else rest[1:]
    else:
        return rest.split("--")[0].strip()


def extract_email_content(tool_input):
    """Extract subject and body from a draft-email or send-email command.

    Returns:
        tuple: (combined_content, body_only) where combined_content is
               subject + body for general checks, and body_only is used
               for structural checks (greeting, sign-off).
    """
    cmd = tool_input.get("command", "")
    subject = extract_flag_value(cmd, "--subject")
    body = extract_flag_value(cmd, "--body")
    combined = " ".join(filter(None, [subject, body])).strip()
    return combined, body.strip()


def audit_log(violations):
    """Log tone violations."""
    if not violations:
        return
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {"timestamp": ts, "violations": violations}
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        hook_input = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    # Only check email-related commands
    if not any(t in cmd for t in ("draft-email", "send-email")):
        sys.exit(0)

    content, body = extract_email_content(tool_input)
    if not content:
        sys.exit(0)

    violations = check_tone(content, body)
    if violations:
        audit_log(violations)
        reasons = [f"- {v['reason']}: \"{v['match']}\"" for v in violations]
        msg = "EMAIL BLOCKED — tone/content violations:\n" + "\n".join(reasons)
        msg += "\n\nRevise the email to fix these issues before sending."
        print(msg, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
