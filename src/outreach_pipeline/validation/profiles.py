"""Validation profiles — named check sets with mode (block/audit).

Each profile defines:
- checks: list of check labels to run (must match labels from tone_guard.check_tone)
- mode: 'block' (fail on violation) or 'audit' (report without failing)
- max_retries: (optional) how many times to retry on violation (block mode only)

Labels from check_tone: PROFANITY, AGGRESSIVE, MANIPULATIVE, DISCRIMINATORY,
FALSE_CLAIMS, TRACKING_DATA, CALL_REQUEST, EM_DASH, FALSE_PROMISE, TEAM_HANDOFF,
CONFIDENTIAL_ASK, TECHNICAL_JARGON, WEAK_CTA, GATEKEEPER, FILLER, OVERSELLING,
PII_PERSON_NAME, PII_EMAIL, PII_PHONE, PII_DEAL_VALUE, NON_ENGLISH,
MISSING_GREETING, MISSING_SIGNOFF, COMBINED_CTA, EXCESSIVE_CAPS
"""

PROFILES: dict[str, dict] = {
    "cold_outreach": {
        "checks": [
            "TRACKING_DATA", "CALL_REQUEST", "FALSE_PROMISE", "GATEKEEPER",
            "WEAK_CTA", "COMBINED_CTA", "EM_DASH", "FILLER",
            "OVERSELLING", "NON_ENGLISH", "PII_PERSON_NAME",
            "PII_EMAIL", "PII_PHONE", "PII_DEAL_VALUE",
            "MISSING_GREETING", "MISSING_SIGNOFF",
            "TEAM_HANDOFF", "CONFIDENTIAL_ASK",
        ],
        "mode": "block",
        "max_retries": 2,
    },
    "retargeting": {
        "checks": [
            "TRACKING_DATA", "FALSE_PROMISE", "WEAK_CTA", "PII_EMAIL",
            "MISSING_GREETING", "MISSING_SIGNOFF",
        ],
        "mode": "block",
        "max_retries": 2,
    },
    "followup": {
        "checks": [
            "TRACKING_DATA", "PII_EMAIL", "PII_PHONE",
        ],
        "mode": "block",
        "max_retries": 1,
    },
    "content_grading": {
        "checks": [
            "PROFANITY", "AGGRESSIVE", "MANIPULATIVE", "FALSE_CLAIMS",
            "OVERSELLING", "TECHNICAL_JARGON",
        ],
        "mode": "audit",
    },
}
