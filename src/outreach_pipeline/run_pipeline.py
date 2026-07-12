#!/usr/bin/env python3
"""Run the outreach pipeline headlessly via Claude Code.

Claude Code manages the subagents (mql-enricher, lead-scorer, email-drafter,
reply-monitor), which run the skills, hooks, and guardrails — same as interactive.

Usage:
  python run_pipeline.py                           # This week
  python run_pipeline.py --start 2026-02-16 --end 2026-02-22
  python run_pipeline.py --weekly                  # Last 7 days

Schedule with cron/Task Scheduler:
  # Every Monday at 8am — process last week's MQLs
  0 8 * * 1 cd /path/to/outreach-pipeline && python run_pipeline.py --weekly
"""
import argparse
import subprocess
import sys
import json
from datetime import datetime, timedelta, timezone


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Run outreach pipeline via Claude Code")
    p.add_argument("--start", help="Start date (YYYY-MM-DD)")
    p.add_argument("--end", help="End date (YYYY-MM-DD)")
    p.add_argument("--weekly", action="store_true", help="Process last 7 days")
    p.add_argument("--daily", action="store_true", help="Process last 24 hours")
    p.add_argument("--dry-run", action="store_true", help="Print the command without running")
    return p.parse_args(args)


def _get_config_value(key, default):
    try:
        from outreach_pipeline.config import load_config
        return load_config().get(key, default)
    except Exception:
        return default


def get_date_range(args):
    """Determine start and end dates."""
    if args.weekly:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=7)
        return str(start), str(end)
    if args.daily:
        end = datetime.now(timezone.utc).date()
        # On Sunday (weekday 6), look back to Friday to cover the weekend
        lookback = 2 if end.weekday() == 6 else 1
        start = end - timedelta(days=lookback)
        return str(start), str(end)
    if args.start and args.end:
        return args.start, args.end
    # Default: this week (Monday to today)
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return str(monday), str(today)


def run(start_date, end_date, dry_run=False):
    """Invoke Claude Code headless with the outreach-pipeline skill."""
    prompt = (
        f"Run /outreach-pipeline {start_date} {end_date}. "
        f"Run the email-drafter in background mode (non-interactive, auto-approve all drafts). "
        f"After completion, export to Excel."
    )

    cmd = [
        "claude",
        "--headless",
        "--allowedTools", "Bash,Read,Write,Skill,Task",
        "-p", prompt,
    ]

    if dry_run:
        print("Would run:")
        print(f"  {' '.join(cmd)}")
        return {"success": True, "dry_run": True}

    print(f"Running pipeline: {start_date} to {end_date}")
    print(f"Claude Code managing subagents headlessly...\n")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_get_config_value("pipeline_timeout_seconds", 1800),
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return {"success": result.returncode == 0, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Pipeline timed out after 30 minutes"}
    except FileNotFoundError:
        return {"success": False, "error": "Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code"}


def main():
    args = parse_args()
    start_date, end_date = get_date_range(args)
    result = run(start_date, end_date, args.dry_run)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
