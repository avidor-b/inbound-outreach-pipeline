"""Audit the intent classifier against a deployment's submission corpus.

Reports, per submission:
  - which regex groups fired
  - whether the decision is "clean" or lands on sales_inquiry with
    zero-fire / partial-non-sales evidence (the LLM-fallback targets)

Useful to:
  1. Re-measure LLM-fallback fire rate as traffic grows.
  2. Spot categories where the regex is drifting (e.g. support
    patterns never firing despite obvious support requests).

Usage:
    python -m scripts.classifier_audit               # all submissions
    python -m scripts.classifier_audit --since 30    # last 30 days
    python -m scripts.classifier_audit --json        # JSON for tooling
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from outreach_pipeline.db.pool import init_pool, get_pool
from outreach_pipeline.policies.submission import (
    _OWNERSHIP, _TROUBLESHOOT, _SW_UPDATE, _STRONG_SUPPORT,
    _EVENT_URL, _MESSAGE_EVENTS,
    _PRODUCT_PATTERNS,
    _VENDOR_PROVIDE, _VENDOR_PARTNERSHIP, _VENDOR_SELF_PROMO,
    _PROFILE_RELEVANCE, _DEFAULT_EXCLUDED_CATEGORY,
    classify_intent,
)


def audit_one(msg: str, url: str) -> dict:
    own = bool(_OWNERSHIP.search(msg))
    trb = bool(_TROUBLESHOOT.search(msg))
    sw = bool(_SW_UPDATE.search(msg))
    sss = bool(_STRONG_SUPPORT.search(msg))
    evu = bool(_EVENT_URL.search(url or ""))
    evm = bool(_MESSAGE_EVENTS.search(msg))
    prod = any(p.search(msg) for p in _PRODUCT_PATTERNS.values())
    vpr = bool(_VENDOR_PROVIDE.search(msg))
    vpa = bool(_VENDOR_PARTNERSHIP.search(msg))
    vsp = bool(_VENDOR_SELF_PROMO.search(msg))
    relevant = bool(_PROFILE_RELEVANCE.search(msg))
    excluded = bool(_DEFAULT_EXCLUDED_CATEGORY.search(msg))

    support_full = sss or (own and (trb or sw))
    vendor_full = (not prod) and (vpr or (vpa and vsp))
    offtopic_full = excluded and not relevant and not prod

    partials = []
    if (own or trb or sw) and not support_full:
        partials.append("support")
    if (vpr or vpa or vsp) and not vendor_full:
        partials.append("vendor")
    if excluded and not offtopic_full:
        partials.append("off_topic")

    any_signal = any([own, trb, sw, sss, evu, evm, prod, vpr, vpa, vsp, relevant, excluded])

    return {
        "fires": {
            k: v for k, v in {
                "ownership": own, "troubleshoot": trb, "sw_update": sw,
                "strong_support": sss, "event_url": evu, "event_msg": evm,
                "product": prod, "vendor_provide": vpr, "vendor_partner": vpa,
                "vendor_promo": vsp, "profile_relevance": relevant,
                "excluded_category": excluded,
            }.items() if v
        },
        "partials": partials,
        "zero_fire": not any_signal,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=None,
                    help="Only audit submissions captured in the last N days.")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON for tooling instead of human-readable output.")
    args = ap.parse_args()

    await init_pool()
    pool = get_pool()
    where = "WHERE message IS NOT NULL AND length(trim(message)) > 0"
    params: list = []
    if args.since:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.since)
        where += " AND captured_at >= $1"
        params.append(cutoff)
    subs = await pool.fetch(
        f"SELECT id, intent_type, form_name, page_url, message, captured_at "
        f"FROM submissions {where} ORDER BY id",
        *params,
    )

    ambiguous = []
    by_partial = Counter()
    zero_fire = 0
    for s in subs:
        rep = audit_one(s["message"] or "", s["page_url"] or "")
        intent = classify_intent(
            s["message"] or "", s["form_name"] or "", s["page_url"] or "",
        )["intent_type"]
        is_ambig = intent == "sales_inquiry" and (rep["zero_fire"] or rep["partials"])
        if is_ambig:
            ambiguous.append({
                "id": s["id"],
                "stored": s["intent_type"],
                "partials": rep["partials"],
                "zero_fire": rep["zero_fire"],
                "fires": list(rep["fires"].keys()),
                "preview": (s["message"] or "")[:120],
            })
        for p in rep["partials"]:
            by_partial[p] += 1
        if rep["zero_fire"]:
            zero_fire += 1

    if args.json:
        print(json.dumps({
            "total": len(subs),
            "zero_fire": zero_fire,
            "by_partial": dict(by_partial),
            "ambiguous_count": len(ambiguous),
            "ambiguous": ambiguous,
        }, default=str, indent=2))
        return 0

    n = len(subs)
    print(f"Audited {n} submissions with non-empty messages")
    if not n:
        return 0
    print(f"  zero-fire:       {zero_fire}  ({100 * zero_fire // n}%)")
    print(f"  partial signals: {sum(by_partial.values())} "
          f"(support={by_partial['support']} vendor={by_partial['vendor']} "
          f"off_topic={by_partial['off_topic']})")
    print(f"  LLM-fallback-triggering submissions: {len(ambiguous)}  "
          f"({100 * len(ambiguous) // n}%)\n")
    for a in ambiguous:
        tag = "zero-fire" if a["zero_fire"] else ",".join(a["partials"]) + "-partial"
        print(f"  #{a['id']:4d} [{tag}] {a['preview']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
