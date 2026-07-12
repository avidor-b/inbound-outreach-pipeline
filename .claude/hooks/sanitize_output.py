#!/usr/bin/env python3
"""PostToolUse hook that redacts sensitive data from tool output before the LLM sees it.

Reads hook input JSON from stdin, scans tool output for sensitive patterns,
redacts matches, and writes sanitized output to stdout.

Exit codes:
  0 — output passed through (possibly with redactions)
  2 — block the tool output entirely (critical sensitive content detected)
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG = Path("data/sanitizer_audit.log")

# --- Patterns to redact ---

CURRENCY = re.compile(
    r'(?:[\$€£¥])\s*\d[\d,]*\.?\d*\s*(?:[KkMmBb](?:illion)?)?'
    r'|'
    r'\d[\d,]*\.?\d*\s*[KkMmBb]?\s*(?:USD|EUR|GBP|ARR|MRR|ACV|TCV)',
    re.IGNORECASE,
)

DEAL_TERMS = re.compile(
    r'\d+[\-\s]*year\s*(?:contract|deal|agreement|term)'
    r'|'
    r'\d+\.?\d*\s*%\s*(?:margin|discount|markup|commission|royalt)',
    re.IGNORECASE,
)

PII_EMAIL = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}')
PII_PHONE = re.compile(r'(?:\+?\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}')
PII_SSN = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
# Match lastname field in JSON ("lastname": "Smith") or plain text (lastname: Smith)
PII_LASTNAME = re.compile(r'("lastname"\s*:\s*)"([^"]*)"', re.IGNORECASE)

CONFIDENTIAL_KW = re.compile(
    r'\b(?:confidential|internal\s+only|do\s+not\s+share|NDA|under\s+NDA|proprietary)\b',
    re.IGNORECASE,
)

PATTERNS = [
    ("CURRENCY", CURRENCY),
    ("DEAL_TERMS", DEAL_TERMS),
    ("PII_EMAIL", PII_EMAIL),
    ("PII_PHONE", PII_PHONE),
    ("PII_SSN", PII_SSN),
    ("CONFIDENTIAL", CONFIDENTIAL_KW),
]

# Patterns where only the value portion should be redacted (key preserved)
KEY_VALUE_PATTERNS = [
    ("PII_LASTNAME", PII_LASTNAME),
]

REDACT_TAG = "[REDACTED:{label}]"


def sanitize(text):
    """Scan text and redact sensitive patterns. Returns (sanitized_text, redactions)."""
    redactions = []
    result = text
    for label, pattern in PATTERNS:
        for match in pattern.finditer(result):
            redactions.append({"label": label, "value": match.group()})
        result = pattern.sub(REDACT_TAG.format(label=label), result)
    for label, pattern in KEY_VALUE_PATTERNS:
        for match in pattern.finditer(result):
            redactions.append({"label": label, "value": match.group(2)})
        result = pattern.sub(lambda m: m.group(1) + '"[REDACTED:PII_LASTNAME]"', result)
    return result, redactions


def audit_log(tool_name, redactions):
    """Append redaction event to audit log."""
    if not redactions:
        return
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "timestamp": ts,
        "tool": tool_name,
        "redactions": [{"label": r["label"], "preview": r["value"][:20] + "..."} for r in redactions],
    }
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
    tool_output = hook_input.get("tool_output", "")

    if not isinstance(tool_output, str):
        tool_output = json.dumps(tool_output)

    sanitized, redactions = sanitize(tool_output)

    if redactions:
        audit_log(tool_name, redactions)
        output = {"sanitized_output": sanitized}
        print(json.dumps(output))

    sys.exit(0)


if __name__ == "__main__":
    main()
