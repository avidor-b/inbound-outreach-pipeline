#!/usr/bin/env python3
"""PreToolUse hook that enforces all pipeline guardrails on email and scrape commands.

Intercepts:
- draft-email / send-email: checks opt-out, duplicate, daily send limit
- scrape-site: checks scrape blocklist

Exit codes:
  0 — proceed
  2 — blocked (violation details returned to Claude)
"""
import json
import re
import sys
from pathlib import Path

# Import guardrails functions
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from outreach_pipeline.db import (
    get_db, check_opt_out, check_duplicate, check_send_limit,
    check_scrape_blocklist, check_events_contact, check_support_request,
)

SCORED_LEADS = Path("data/scored_leads.json")
ENRICHED_MQLS = Path("data/enriched_mqls.json")


def get_mql_date(contact_id):
    """Look up the MQL date for a contact from SQLite or JSON fallback."""
    try:
        conn = get_db()
        row = conn.execute("SELECT properties FROM contacts WHERE id=?", (str(contact_id),)).fetchone()
        conn.close()
        if row:
            import json as _json
            props = _json.loads(row["properties"]) if isinstance(row["properties"], str) else row["properties"]
            return props.get("hs_v2_date_entered_marketingqualifiedlead", "")
    except Exception:
        pass
    # JSON fallback
    for path in (SCORED_LEADS, ENRICHED_MQLS):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for c in data.get("contacts", []):
            if c.get("id") == str(contact_id):
                return c.get("properties", {}).get("hs_v2_date_entered_marketingqualifiedlead", "")
    return None


def extract_flag(cmd, flag):
    """Extract value after a --flag from a command string."""
    pattern = rf'{flag}\s+["\']?([^"\'\s]+)["\']?'
    m = re.search(pattern, cmd)
    return m.group(1) if m else None


def check_enrichment_complete():
    """Block scoring if any contacts from the most recent batch haven't been enriched.

    Uses the most recent fetched_at date rather than date('now') so the gate still
    fires even when scoring is run the day after fetching.
    Returns (blocked, reason).
    """
    try:
        conn = get_db()
        batch_row = conn.execute(
            "SELECT date(MAX(fetched_at)) as latest FROM contacts"
        ).fetchone()
        if not batch_row or not batch_row["latest"]:
            conn.close()
            return False, ""
        latest_batch = batch_row["latest"]
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM contacts
               WHERE enriched_at IS NULL
               AND date(fetched_at) = ?""",
            (latest_batch,),
        ).fetchone()
        conn.close()
        count = row["cnt"] if row else 0
        if count > 0:
            return True, (
                f"ENRICHMENT INCOMPLETE: {count} contact(s) from the {latest_batch} batch "
                f"have not been enriched yet. Run the mql-enricher until "
                f"`pipeline-db leads --today --unenriched` returns empty before scoring."
            )
    except Exception:
        pass
    return False, ""


def _get_min_score():
    """Read min_score_to_email from config.yaml, with fallback to 50."""
    try:
        from outreach_pipeline.config import load_config
        return load_config().get("min_score_to_email", 50)
    except Exception:
        return 50


def check_drafting_complete():
    """Block monitoring/export if eligible contacts from the most recent batch haven't been drafted.

    Uses the most recent fetched_at date rather than date('now') so the gate still
    fires even when export/monitor is run the day after fetching (e.g. overnight runs).
    Returns (blocked, reason).
    """
    min_score = _get_min_score()
    try:
        conn = get_db()
        # Find the most recent batch date
        batch_row = conn.execute(
            "SELECT date(MAX(fetched_at)) as latest FROM contacts WHERE scored_at IS NOT NULL"
        ).fetchone()
        if not batch_row or not batch_row["latest"]:
            conn.close()
            return False, ""
        latest_batch = batch_row["latest"]
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM contacts
               WHERE score >= ?
               AND scored_at IS NOT NULL
               AND date(fetched_at) = ?
               AND id NOT IN (SELECT contact_id FROM drafts)
               AND id NOT IN (SELECT contact_id FROM send_log)""",
            (min_score, latest_batch),
        ).fetchone()
        conn.close()
        count = row["cnt"] if row else 0
        if count > 0:
            return True, (
                f"DRAFTING INCOMPLETE: {count} contact(s) with score ≥{min_score} from the "
                f"{latest_batch} batch have not been drafted yet. Run the email-drafter until "
                f"all eligible contacts have drafts before monitoring replies or exporting."
            )
    except Exception:
        pass
    return False, ""


def check_scoring_complete():
    """Block drafting if contacts from the most recent batch have never been through the scorer.

    Specifically catches the case where the scorer hit its context limit mid-batch
    and left contacts with scored_at IS NULL (never scored at all). Contacts that
    were scored and received a low/zero score intentionally are not blocked.
    Uses the most recent fetched_at date rather than date('now').
    Returns (blocked, reason).
    """
    try:
        conn = get_db()
        batch_row = conn.execute(
            "SELECT date(MAX(fetched_at)) as latest FROM contacts WHERE enriched_at IS NOT NULL"
        ).fetchone()
        if not batch_row or not batch_row["latest"]:
            conn.close()
            return False, ""
        latest_batch = batch_row["latest"]
        row = conn.execute(
            """SELECT COUNT(*) as cnt FROM contacts
               WHERE enriched_at IS NOT NULL
               AND scored_at IS NULL
               AND date(fetched_at) = ?""",
            (latest_batch,),
        ).fetchone()
        conn.close()
        count = row["cnt"] if row else 0
        if count > 0:
            return True, (
                f"SCORING INCOMPLETE: {count} contact(s) from the {latest_batch} batch are enriched but have never been scored. "
                f"Run the lead-scorer until `pipeline-db leads --today --unscored` returns empty before drafting."
            )
    except Exception:
        pass
    return False, ""


def check_email_guardrails(cmd):
    """Run all email guardrails. Returns (blocked, reasons)."""
    contact_id = extract_flag(cmd, "--contact-id")
    if not contact_id:
        return False, []

    conn = get_db()
    reasons = []

    blocked, reason = check_events_contact(conn, contact_id)
    if blocked:
        reasons.append(f"EVENTS CONTACT: {reason}")

    blocked, reason = check_support_request(conn, contact_id)
    if blocked:
        reasons.append(f"SUPPORT REQUEST: {reason}")

    blocked, reason = check_opt_out(conn, contact_id)
    if blocked:
        reasons.append(f"OPT-OUT: {reason}")

    mql_date = get_mql_date(contact_id)
    blocked, reason = check_duplicate(conn, contact_id, mql_date=mql_date)
    if blocked:
        reasons.append(f"DUPLICATE: {reason}")

    blocked, reason, count = check_send_limit(conn)
    if blocked:
        reasons.append(f"SEND LIMIT: {reason}")

    conn.close()
    return len(reasons) > 0, reasons


def check_scrape_guardrails(cmd):
    """Run scrape guardrails. Returns (blocked, reasons)."""
    url = extract_flag(cmd, "--url")
    if not url:
        return False, []

    domain = re.sub(r'^https?://', '', url).split('/')[0]
    conn = get_db()
    blocked, reason = check_scrape_blocklist(conn, domain)
    conn.close()
    if blocked:
        return True, [f"SCRAPE BLOCKED: {reason}"]
    return False, []


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        hook_input = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    # Dispatch based on the first token of the command so that pipeline command
    # names appearing inside git commit messages, grep patterns, or file content
    # don't accidentally trigger guardrail checks.
    first = cmd.strip().split()[0] if cmd.strip() else ""

    if first in ("draft-email", "send-email"):
        if "--to " in cmd:
            if "--contact-id" in cmd:  # block: can't combine --to with a real contact
                print("BLOCKED by pipeline guardrails:\n- --to and --contact-id are mutually exclusive. "
                      "Use --to only for test sends without a contact.", file=sys.stderr)
                sys.exit(2)
            sys.exit(0)  # test send — skip guardrails

        # Block drafting/sending if scoring is not complete
        blocked, reason = check_scoring_complete()
        if blocked:
            print(f"BLOCKED by pipeline guardrails:\n- {reason}", file=sys.stderr)
            sys.exit(2)

        blocked, reasons = check_email_guardrails(cmd)
    elif first == "python" and "score_contact" in cmd:
        # Scorer invokes score_contact via `python -c "...score_contact(...)"` —
        # match on first token to avoid false positives in grep/cat output.
        blocked, reason = check_enrichment_complete()
        if blocked:
            print(f"BLOCKED by pipeline guardrails:\n- {reason}", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    elif first in ("monitor-replies", "export-pipeline"):
        # Block monitoring/export if drafting isn't complete for today's eligible contacts
        blocked, reason = check_drafting_complete()
        if blocked:
            print(f"BLOCKED by pipeline guardrails:\n- {reason}", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    elif first == "scrape-site":
        blocked, reasons = check_scrape_guardrails(cmd)
    elif first == "run-recipe":
        # Recipes internalize their own guardrails via operator middleware.
        # The hook passes through to avoid double-checking.
        sys.exit(0)
    else:
        sys.exit(0)

    if blocked:
        msg = "BLOCKED by pipeline guardrails:\n" + "\n".join(f"- {r}" for r in reasons)
        msg += "\n\nSkip this contact/domain and move to the next."
        print(msg, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
