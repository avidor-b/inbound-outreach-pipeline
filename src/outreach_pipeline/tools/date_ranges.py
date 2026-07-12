"""Runtime date resolution for date-aware tools.

Keep concrete dates out of long-lived system prompts: a production
process can run for days, but relative dates must be resolved when the
tool executes and in the business/report timezone.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _local_today(
    *,
    now: datetime | None = None,
    timezone_name: str = "America/New_York",
) -> datetime.date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(ZoneInfo(timezone_name)).date()


def resolve_date_expr(
    value: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "America/New_York",
) -> str:
    """Resolve one date expression to YYYY-MM-DD.

    Already-concrete ISO dates pass through. Relative dates are resolved
    in the report timezone, not the server timezone.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw

    today = _local_today(now=now, timezone_name=timezone_name)
    text = raw.lower().strip()

    if text in {"today", "now"}:
        return today.isoformat()
    if text == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if text == "tomorrow":
        return (today + timedelta(days=1)).isoformat()

    weekday = _WEEKDAYS.get(text.removeprefix("last ").strip())
    if weekday is not None:
        days_back = (today.weekday() - weekday) % 7
        if days_back == 0:
            days_back = 7
        return (today - timedelta(days=days_back)).isoformat()

    return raw


def resolve_date_range(
    start_date: str = "",
    end_date: str = "",
    *,
    default: str = "",
    now: datetime | None = None,
    timezone_name: str = "America/New_York",
) -> tuple[str, str]:
    """Resolve a date range to concrete inclusive YYYY-MM-DD strings."""
    start_raw = (start_date or default or "").strip()
    end_raw = (end_date or "").strip()
    text = start_raw.lower()
    today = _local_today(now=now, timezone_name=timezone_name)

    if text == "last week":
        this_monday = today - timedelta(days=today.weekday())
        start = this_monday - timedelta(days=7)
        end = this_monday - timedelta(days=1)
        return start.isoformat(), end.isoformat()

    if text == "this week":
        return (today - timedelta(days=today.weekday())).isoformat(), today.isoformat()

    m = re.fullmatch(r"last\s+(\d+)\s+days?", text)
    if m:
        days = max(int(m.group(1)), 1)
        return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()

    start = resolve_date_expr(start_raw, now=now, timezone_name=timezone_name)
    end = resolve_date_expr(end_raw, now=now, timezone_name=timezone_name) if end_raw else start
    return start, end
