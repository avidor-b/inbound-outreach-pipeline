"""Seed fixtures into the local staging stack.

Idempotent: running twice produces the same end state. Covers one
engagement in each status we care about for regression tests, plus a
user row the pre-commit hook mints JWTs against.

Run once after `docker compose -f docker-compose.staging.yml up`:
    python scripts/seed_staging.py

Connects to the staging Postgres at localhost:5533 by default; override
with STAGING_DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import uuid

STAGING_URL = os.environ.get(
    "STAGING_DATABASE_URL",
    "postgresql://pipeline:pipeline@localhost:5533/outreach",
)
# Must match the JWT_SECRET in docker-compose.staging.yml.
TEST_USER_ID = "00000000-0000-4000-8000-000000000001"
TEST_USER_EMAIL = "test@example.com"


async def main() -> None:
    os.environ["DATABASE_URL"] = STAGING_URL
    from datetime import datetime, timedelta, timezone
    from outreach_pipeline.db.pool import init_pool, close_pool, get_pool
    from outreach_pipeline.db.contacts import upsert_contact
    from outreach_pipeline.db.engagements import create_engagement, add_submission
    from outreach_pipeline.db.submissions import create_submission
    from outreach_pipeline.db.drafts import save_draft
    from outreach_pipeline.db.threads import create_user
    from outreach_pipeline.organization import get_organization_profile

    await init_pool(database_url=STAGING_URL)
    pool = get_pool()

    # --- teardown: wipe prior stg-* state so every run is reproducible ---
    await pool.execute("DELETE FROM drafts WHERE contact_id LIKE 'stg-%'")
    await pool.execute(
        "DELETE FROM engagement_submissions WHERE engagement_id IN "
        "(SELECT id FROM engagements WHERE contact_id LIKE 'stg-%')"
    )
    await pool.execute("DELETE FROM engagements WHERE contact_id LIKE 'stg-%'")
    await pool.execute("DELETE FROM submissions WHERE contact_id LIKE 'stg-%'")
    await pool.execute("DELETE FROM contacts WHERE id LIKE 'stg-%'")
    print("  · teardown: cleared previous stg-* fixtures")

    # --- user ----------------------------------------------------- #
    # Fixed bcrypt hash for the staging password "staging-password".
    # Anyone with access to the machine already has access to the
    # staging DB; the point is convenience for manual browsing.
    import bcrypt
    password_hash = bcrypt.hashpw(b"staging-password", bcrypt.gensalt()).decode()
    existing = await pool.fetchrow("SELECT id FROM users WHERE id=$1", TEST_USER_ID)
    if not existing:
        await create_user(
            TEST_USER_ID, TEST_USER_EMAIL, "Test",
            role="user", password_hash=password_hash,
        )
        print(f"  + user {TEST_USER_EMAIL} (password: staging-password)")
    else:
        # Refresh email, name, and hash in-place (FK constraints on
        # threads prevent us from re-creating the row).
        await pool.execute(
            """UPDATE users
               SET email=$1, name=$2, password_hash=$3
               WHERE id=$4""",
            TEST_USER_EMAIL, "Test", password_hash, TEST_USER_ID,
        )
        print(f"  = user refreshed: {TEST_USER_EMAIL} (password: staging-password)")

    # --- contacts + engagements ----------------------------------- #
    # Seed a deterministic set: contact IDs are fixed strings so re-running
    # updates in place rather than duplicating.
    # Five fictional companies across distinct verticals — obviously
    # placeholder names so the staging DB is safe for demos without
    # redaction. Each has their own pages visited, multi-submission
    # history where relevant, and realistic prospect-style messages
    # (typos, casual tone, specific technical details).
    #
    # The flow test drives through enrich → score → draft on every
    # run, so these start un-scored and un-drafted.
    #
    # Each SEED entry: (contact_id, company, industry, firstname,
    #   lastname, jobtitle, submissions). submissions is a list of
    #   (form_name, page_url, page_title, message) tuples, most
    #   recent last. The most recent message ends up on the contact
    #   row as the canonical HubSpot "message" property.
    profile = get_organization_profile()
    primary_domain = profile.primary_domains[0] if profile.primary_domains else "example.com"
    website = f"https://www.{primary_domain}"

    SEEDS = [
        # --- 1. terse "please call me" — the bare-bones lead ---
        ("stg-c1", "Example Public Safety Department", "government public safety",
         "Patricia", "Nguyen", "Communications Systems Manager",
         [
             ("Resource Download",
              f"{website}/resources/public-safety-brochure",
              "Public Safety Brochure",
              ""),
             ("Contact Us",
              f"{website}/solutions/public-safety",
              "Mission-Critical Wireless for Public Safety",
              "Please contact me regarding wireless for our new "
              "dispatch center."),
         ]),

        # --- 2. medium-length — typical inbound ---
        ("stg-c2", "Example Mobile Networks", "mobile network operator",
         "Marco", "Silva", "RF Planning Lead",
         [
             ("Contact Sales",
              f"{website}/products/radio-b100",
              "RADIO-B100 — High-capacity E-band radio",
              "Evaluating mmWave backhaul for a 5G rollout. Looking for "
              "10 Gbps per hop. Do you have a sales contact for Brazil?"),
         ]),

        # --- 3. one-liner, somewhat generic ---
        ("stg-c3", "Example Retail Group", "retail chain",
         "Dana", "Okafor", "Director of IT Infrastructure",
         [
             ("Content Download",
              f"{website}/case-studies/retail-warehouses",
              "Case Study: Wireless Redundancy for DCs",
              ""),
             ("Contact Us",
              f"{website}/solutions/enterprise-networks",
              "Enterprise Networks Solution Brief",
              "Interested in wireless redundancy for our distribution "
              "centers."),
         ]),

        # --- 4. long and detailed — the dream prospect ---
        ("stg-c4", "Example Industrial Campus", "enterprise private network",
         "Jin-Ho", "Park", "Head of Network Security",
         [
             ("Demo Request",
              f"{website}/solutions/private-networks",
              "Private 4G/5G Networks",
              "We're evaluating private 5G for our R&D campus (40 "
              "buildings across ~200 acres). Our integrator has "
              f"shortlisted {profile.name} alongside two competitors. Key "
              "requirements: FIPS-certified encryption end-to-end, "
              "air-gapped OAM, no cloud dependency, and integration "
              "with our existing identity platform. Timeline is "
              "aggressive — we're aiming for first-phase cutover in "
              "Q2 next year. Can we get a 90-minute technical session "
              "with your SE team plus one of your private-network "
              "references? Happy to sign NDA."),
         ]),

        # --- 5. casual, conversational WISP ---
        ("stg-c5", "Example Rural Networks", "wireless internet service provider",
         "Reese", "Tanaka", "Owner / Chief Engineer",
         [
             ("Blog Signup",
              f"{website}/blog/mesh-radio-wisp-deployment",
              "Blog: WISP uses mesh radios to extend reach",
              ""),
             ("Partner Inquiry",
              f"{website}/products/mesh-radio",
              "mesh radio — 60GHz mesh",
              "hey — small WISP in rural Ontario, bumping into "
              "distance limits with our current gear past 3km LOS. "
              "Your mesh radio platform looks interesting. Any reseller pricing?"),
         ]),
    ]

    # fetched_at = yesterday (relative to UTC) so "show yesterday's
    # leads" in the flow test picks these up.
    yesterday = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)

    for cid, company, industry, firstname, lastname, jobtitle, subs in SEEDS:
        # The "canonical" message on the contact row is the message
        # from the most recent submission with content. This mirrors
        # how HubSpot's property gets overwritten by each form fill.
        canonical_message = next(
            (m for (_f, _u, _t, m) in reversed(subs) if m), "",
        )
        # First page visited kicks off the HubSpot analytics attribution.
        first_url = subs[0][1]

        props = {
            "company": company,
            "email": f"{firstname.lower()}.{lastname.lower()}@{cid}.example.com",
            "firstname": firstname,
            "lastname": lastname,
            "country": "United States",
            "region": "NORTH AMERICA",
            "jobtitle": jobtitle,
            "industry": industry,
            "email_domain": f"{cid}.example.com",
            "hs_analytics_first_url": first_url,
            "hs_analytics_num_page_views": str(len(subs) * 2 + 1),
            "hs_email_open": "1",
            "hs_email_click": "0",
            "message": canonical_message,
            # MQL date (what get_leads filters on for `date=yesterday`).
            # HubSpot stores UTC; the date filter converts to
            # report_timezone (America/New_York). Stamp mid-afternoon
            # UTC so it falls on the right day in ET.
            "hs_v2_date_entered_marketingqualifiedlead": yesterday.isoformat(),
            "createdate": yesterday.isoformat(),
            "lifecyclestage": "marketingqualifiedlead",
        }
        await upsert_contact(cid, props, fetched_at=yesterday)

        # Pre-populate enrichment so the flow-test enrich stage is fast
        # (it short-circuits on existing company_context). Scoring +
        # drafting still run real LLMs during the test.
        ctx = (
            f"{company} operates in {industry}. "
            "Relevant to wireless/private-network solutions "
            "(mm-wave, e-band, 5G/LTE private networks)."
        )
        await pool.execute(
            """UPDATE contacts
               SET company_context=$1,
                   enrichment_confidence='high',
                   enriched_at=$2
               WHERE id=$3""",
            ctx, yesterday, cid,
        )

        # Create the engagement in "pending_approval" so the paired
        # draft (saved below with status="approved") reflects the
        # natural post-review state: the drafter produced an email,
        # the human clicked Approve & Save on the interrupt. With
        # the middleware fix the engagement would now auto-advance
        # to `approved`, but seed-time we want the mid-review state
        # on the card. Demo-spread SQL moves specific engagements
        # further (approved/sdr_handoff/blocked) as needed.
        eid = await create_engagement(
            cid, kind="sales", status="pending_approval", primary_product="",
        )
        # Back-date opened_at to yesterday so date-filtered stats
        # group these under "yesterday" consistently.
        await pool.execute(
            "UPDATE engagements SET opened_at=$1 WHERE id=$2",
            yesterday, eid,
        )

        # Create one submission per (form_name, page_url, message)
        # tuple so "show submissions" renders history and "visited
        # pages" on drafts renders multiple URLs.
        sub_ids: list[int] = []
        for i, (form_name, page_url, page_title, msg) in enumerate(subs):
            # Spread submissions over the past two days so the history
            # has a timeline feel rather than all identical timestamps.
            sub_date = (yesterday - timedelta(days=len(subs) - 1 - i)).date().isoformat()
            sub_id = await create_submission(
                cid, message=msg,
                form_name=form_name, form_id=f"form-{cid}-{i + 1}",
                page_url=page_url, page_title=page_title,
                mql_date=sub_date,
            )
            await add_submission(eid, sub_id)
            sub_ids.append(sub_id)

        # Also pre-seed an approved draft. This mirrors the real
        # post-interrupt state: ReviewMiddleware._areview saves the
        # draft as `approved` but does NOT transition the engagement,
        # so the natural pair is (engagement=pending_approval,
        # draft=approved). The engagement only moves to `approved`
        # when someone explicitly calls update_engagement_status —
        # or when the "Mark as sent" button walks it to `sent`.
        draft_subject = f"Quick question about your wireless project — {company}"
        draft_body = (
            f"Hi {firstname},\n\n"
            f"Thanks for reaching out about your {industry} needs. "
            f"I took a look at the pages you visited and the details "
            f"in your message, and I think our team can help. "
            f"Could we set up a 20-minute call this week to go over "
            f"the specifics?\n\nBest,\nThe Outreach Team"
        )
        await save_draft(
            cid, subject=draft_subject, body=draft_body,
            engagement_id=eid, status="approved",
        )

        print(
            f"  = {cid:8s} eng#{eid} status=pending_approval "
            f"{len(sub_ids)} submissions (forms: "
            f"{', '.join(f for (f, _, _, _) in subs)})"
        )

    print()
    print("Staging fixtures ready.")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
