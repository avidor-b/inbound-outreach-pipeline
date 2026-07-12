#!/usr/bin/env python3
"""PostToolUse hook that validates saved email drafts against tone/content rules.

Fires after draft-email completes successfully. Reads the saved draft from
SQLite and re-runs all tone checks on the stored content. This catches any
edge cases where the PreToolUse hook passed but the saved content contains
violations (e.g. encoding issues, truncation, or agents that bypass --body).

Exit codes:
  0 — draft is clean, or not applicable
  2 — draft has violations (Claude must delete and re-draft)
"""
import json
import re
import sys
from pathlib import Path

# Resolve paths relative to repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tone_guard import check_tone  # reuse all regex checks


def extract_contact_id(cmd):
    m = re.search(r'--contact-id\s+["\']?(\d+)["\']?', cmd)
    return m.group(1) if m else None


def get_saved_draft(contact_id):
    """Fetch subject + body from the drafts table."""
    try:
        from outreach_pipeline.db import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT subject, body FROM drafts WHERE contact_id = ?",
            (str(contact_id),),
        ).fetchone()
        conn.close()
        if row:
            return row["subject"] or "", row["body"] or ""
    except Exception:
        pass
    return None, None


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        hook_input = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    # Extract original command from tool_input (available in PostToolUse hooks)
    tool_input = hook_input.get("tool_input", {})
    cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    # Only validate draft-email saves (not test sends via --to)
    if "draft-email" not in cmd or "--to " in cmd:
        sys.exit(0)

    contact_id = extract_contact_id(cmd)
    if not contact_id:
        sys.exit(0)

    subject, body = get_saved_draft(contact_id)
    if subject is None:
        sys.exit(0)  # Draft not found — nothing to validate

    combined = f"{subject} {body}".strip()
    violations = check_tone(combined, body)

    if violations:
        reasons = [f"- {v['reason']}: \"{v['match']}\"" for v in violations]
        msg = (
            f"DRAFT SAVED BUT FAILED QUALITY CHECK (contact {contact_id}) — violations:\n"
            + "\n".join(reasons)
            + "\n\nThe draft has been saved with errors. Re-draft the email to fix these issues "
            + "(draft-email overwrites on the same contact_id)."
        )
        print(msg, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
