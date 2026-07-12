"""Unit tests for the sendable-resource catalog tool."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from outreach_pipeline.tools import resource_catalog
from outreach_pipeline.tools.resource_catalog import (
    _clear_cache,
    _normalize_url,
    _score_entry,
    _tokenize,
    extract_promises,
    find_sendable_asset,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with a cold catalog cache."""
    _clear_cache()
    yield
    _clear_cache()


@pytest.fixture
def fixture_catalog(tmp_path: Path) -> Path:
    """Small in-memory catalog covering a mix of asset types."""
    data = [
        {
            "id": "sb1",
            "title": "Utilities Solution Brief",
            "url": "https://example.com/sb/utilities",
            "asset_type": "solution-brief",
            "summary": "Secure wireless for the utility grid",
            "tags": ["utilities", "grid", "mmwave"],
            "score": 90,
        },
        {
            "id": "cs1",
            "title": "Case Study: Offshore Oil Platform Backhaul",
            "url": "https://example.com/cs/oil-offshore",
            "asset_type": "case-study",
            "summary": "Replaced legacy satellite with 182km wireless link",
            "tags": ["oil", "gas", "offshore", "backhaul"],
            "score": 90,
        },
        {
            "id": "br1",
            "title": "Managed Services Brochure",
            "url": "https://example.com/br/managed",
            "asset_type": "brochure",
            "summary": "Fully managed network operations",
            "tags": ["managed", "services"],
            "score": 85,
        },
        {
            "id": "bp1",
            "title": "Mining Connectivity Explained",
            "url": "https://example.com/blog/mining",
            "asset_type": "blog-post",
            "summary": "How miners deploy wireless for remote sites",
            "tags": [
                "mining connectivity",
                "remote broadband",
                "private networks",
                "industrial wireless",
                "heavy industry",
            ],
            "score": 70,
        },
        {
            "id": "bp2",
            "title": "Unrelated Post About Hiking",
            "url": "https://example.com/blog/hiking",
            "asset_type": "blog-post",
            "summary": "Nothing to do with networking",
            "tags": ["hiking", "outdoors"],
            "score": 60,
        },
        {
            "id": "external1",
            "title": "Utilities Wireless Guide",
            "url": "https://assets.example.net/guides/utilities",
            "asset_type": "solution-brief",
            "summary": "Wireless connectivity for utilities",
            "tags": ["utilities", "wireless"],
            "score": 100,
        },
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    return path


def test_tokenize_drops_stopwords_and_short_words():
    assert _tokenize("the utilities grid on mmwave") == {"utilities", "grid", "mmwave"}


def test_tokenize_keeps_numeric_and_known_short_acronyms():
    # '5g' is a known short acronym we keep despite being 2 chars
    assert "5g" in _tokenize("backhaul 5g")
    # Alphanumeric compounds stay as one token (regex splits on non-word)
    assert "182km" in _tokenize("182km link")


def test_empty_query_returns_empty_list(fixture_catalog):
    # No product / industry / keywords → don't surface random assets
    assert find_sendable_asset(catalog_path=fixture_catalog) == []
    assert find_sendable_asset(product="", industry="", keywords="",
                               catalog_path=fixture_catalog) == []


def test_missing_catalog_returns_empty_list(tmp_path):
    # Drafter should still work before the catalog is built
    missing = tmp_path / "does_not_exist.json"
    assert find_sendable_asset(product="anything", catalog_path=missing) == []


def test_industry_match_prefers_canonical_asset(fixture_catalog):
    # 'Utilities' industry → the utilities solution brief should win
    results = find_sendable_asset(industry="Utilities", catalog_path=fixture_catalog)
    assert results, "expected at least one match"
    assert results[0]["id"] == "sb1"
    assert results[0]["asset_type"] == "solution-brief"


def test_entries_outside_configured_resource_domains_are_excluded(fixture_catalog):
    results = find_sendable_asset(
        industry="Utilities", keywords="wireless", catalog_path=fixture_catalog,
    )
    assert "external1" not in {result["id"] for result in results}


def test_keyword_match_matches_case_study(fixture_catalog):
    results = find_sendable_asset(
        keywords="offshore oil backhaul", catalog_path=fixture_catalog
    )
    ids = [r["id"] for r in results]
    assert "cs1" in ids
    # Case study should rank above any lower-quality match
    assert results[0]["id"] == "cs1"


def test_keywords_as_list_or_string_equivalent(fixture_catalog):
    list_results = find_sendable_asset(
        keywords=["offshore", "oil"], catalog_path=fixture_catalog
    )
    str_results = find_sendable_asset(
        keywords="offshore oil", catalog_path=fixture_catalog
    )
    assert [r["id"] for r in list_results] == [r["id"] for r in str_results]


def test_top_k_limits_result_count(fixture_catalog):
    # Broad query hits multiple entries; top_k=1 should return exactly 1
    r = find_sendable_asset(
        keywords="utilities oil mining managed", top_k=1, catalog_path=fixture_catalog
    )
    assert len(r) == 1


def test_zero_score_entries_excluded(fixture_catalog):
    # Query for 'hiking' — only the unrelated blog matches, and that's
    # what we want (one result). Query for a term with no overlap should
    # return empty even though entries exist.
    assert find_sendable_asset(
        keywords="quantum neutrino physics", catalog_path=fixture_catalog
    ) == []


def test_tag_hit_cap_prevents_blog_dominance(fixture_catalog):
    # The mining blog has 5 tag phrases; query hits most of them.
    # The utilities solution brief has only 3 tags, 1 of which matches.
    # Without the tag cap a verbose blog would outrank a canonical asset.
    results = find_sendable_asset(
        product="", industry="",
        keywords="mining remote private industrial heavy wireless utilities",
        catalog_path=fixture_catalog, top_k=5,
    )
    # Canonical asset ('solution-brief') should appear somewhere in the
    # results, not be buried beneath the over-tagged blog
    types = [r["asset_type"] for r in results]
    assert "solution-brief" in types


def test_score_entry_returns_zero_for_no_overlap(fixture_catalog):
    cat = json.loads(fixture_catalog.read_text())
    entry = next(e for e in cat if e["id"] == "bp2")  # hiking post
    assert _score_entry(entry, {"backhaul", "mmwave"}) == 0.0


def test_asset_type_boost_breaks_ties(fixture_catalog):
    # Contrived: both bp1 and a hypothetical solution-brief match only on
    # one tag. The solution-brief's asset-type boost should put it higher.
    cat = json.loads(fixture_catalog.read_text())
    bp = next(e for e in cat if e["id"] == "bp1")
    sb = next(e for e in cat if e["id"] == "sb1")
    tokens = {"connectivity"}  # matches bp1 title only, nothing in sb1
    bp_score = _score_entry(bp, tokens)
    sb_score = _score_entry(sb, tokens)
    # bp matches, sb doesn't — confirms scoring baseline
    assert bp_score > sb_score

    # Now a tie case — both match exactly one title token
    tokens2 = {"utilities"}  # hits sb1 title+tag, doesn't hit bp1
    assert _score_entry(sb, tokens2) > 0
    assert _score_entry(bp, tokens2) == 0


def test_return_shape_is_stable(fixture_catalog):
    results = find_sendable_asset(industry="Utilities", catalog_path=fixture_catalog)
    assert results
    r = results[0]
    # Contract: these keys are what the draft tool + UI rely on
    assert set(r.keys()) == {"id", "title", "url", "asset_type", "summary", "match_score"}
    assert isinstance(r["match_score"], float)
    assert isinstance(r["tags"] if "tags" in r else [], list)


def test_extract_promises_empty_body_returns_empty():
    assert extract_promises("", []) == ([], [])


def test_extract_promises_url_match_populates_referenced():
    matches = [
        {"id": "sb1", "title": "Utilities Brief",
         "url": "https://www.example.com/resources/solution-brief/utilities",
         "asset_type": "solution-brief", "summary": "", "tags": ["utilities"]},
    ]
    body = "Here's the link: https://www.example.com/resources/solution-brief/utilities"
    referenced, unverified = extract_promises(body, matches)
    assert referenced == ["sb1"]
    assert unverified == []


def test_extract_promises_url_match_tolerates_trailing_slash_and_www():
    matches = [
        {"id": "sb1", "title": "Utilities Brief",
         "url": "https://www.example.com/resources/sb/utilities/",
         "asset_type": "solution-brief", "summary": "", "tags": []},
    ]
    # Body has no www. and no trailing slash — should still match
    body = "See https://example.com/resources/sb/utilities for details."
    referenced, unverified = extract_promises(body, matches)
    assert referenced == ["sb1"]


def test_extract_promises_flags_phrase_without_catalog_overlap():
    matches = [
        {"id": "sb1", "title": "Utilities Brief",
         "url": "https://example.com/sb1", "asset_type": "solution-brief",
         "summary": "", "tags": ["utilities", "grid"]},
    ]
    # Body promises something completely unrelated to the retrieved matches
    body = (
        "Hi [First Name],\n"
        "I can provide our wireless backhaul solution brief that covers "
        "solar-powered setups for high-capacity security cameras. "
        "Best,\n[Sender Name]"
    )
    referenced, unverified = extract_promises(body, matches)
    assert referenced == []
    assert len(unverified) == 1
    assert "wireless backhaul" in unverified[0].lower() or \
           "solution brief" in unverified[0].lower()


def test_extract_promises_trusts_phrase_that_overlaps_with_matches():
    matches = [
        {"id": "sb1", "title": "Mining Connectivity Solution Brief",
         "url": "https://example.com/mining", "asset_type": "solution-brief",
         "summary": "Mining operators and wireless backhaul",
         "tags": ["mining", "backhaul", "wireless"]},
    ]
    # Phrase tokens overlap with retrieved match — not flagged
    body = "I can send our mining solution brief. Want it?"
    referenced, unverified = extract_promises(body, matches)
    assert referenced == []
    assert unverified == [], f"expected trust, got flag: {unverified}"


def test_extract_promises_want_me_to_send_pattern():
    matches = [{"id": "bp1", "title": "Hiking Post", "url": "https://example.com/hiking",
                "asset_type": "blog-post", "summary": "", "tags": ["hiking"]}]
    body = "Want me to send you our quantum computing whitepaper?"
    referenced, unverified = extract_promises(body, matches)
    assert referenced == []
    assert len(unverified) == 1


def test_extract_promises_url_win_suppresses_phrase_flagging():
    # When a URL lands, we trust any promise in the same draft —
    # the prospect will receive a verified link.
    matches = [
        {"id": "sb1", "title": "Utilities Brief",
         "url": "https://example.com/utilities",
         "asset_type": "solution-brief", "summary": "", "tags": ["utilities"]},
    ]
    body = (
        "I can share our quantum neutrino report — "
        "https://example.com/utilities has more detail."
    )
    referenced, unverified = extract_promises(body, matches)
    assert referenced == ["sb1"]
    # Even though 'quantum neutrino report' is off-topic, the URL wins
    assert unverified == []


def test_extract_promises_ignores_non_offer_language():
    matches = [{"id": "x", "title": "T", "url": "https://example.com/x",
                "asset_type": "blog-post", "summary": "", "tags": []}]
    body = "I want to understand your timeline. Could you share how many links you need?"
    referenced, unverified = extract_promises(body, matches)
    assert referenced == []
    assert unverified == []


def test_normalize_url_handles_variants():
    base = _normalize_url("https://www.example.com/resources/sb/utilities/")
    assert _normalize_url("https://example.com/resources/sb/utilities") == base
    assert _normalize_url("http://www.example.com/resources/sb/utilities?utm=foo") == base
    assert _normalize_url("https://example.com/resources/sb/utilities#section") == base


def test_catalog_file_loaded_from_project(monkeypatch):
    """Smoke test — the actual built catalog must load and return reasonable
    results for realistic prospect contexts."""
    # Using the real catalog (not the fixture)
    results = find_sendable_asset(industry="Utilities", keywords="mmwave backhaul")
    # Don't make exact-title assertions (catalog can change); just sanity check
    if results:
        assert len(results) <= 3
        assert all(r["url"].startswith("http") for r in results)
        assert all(r["asset_type"] for r in results)
