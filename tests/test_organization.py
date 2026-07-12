"""Tests for deployment-specific organization settings."""
from __future__ import annotations

import json

import pytest

from outreach_pipeline.organization import (
    build_organization_profile,
    get_organization_profile,
    get_product_patterns,
    get_url_product_patterns,
    reset_organization_profile_cache,
)


@pytest.fixture(autouse=True)
def _reset_profile_cache():
    reset_organization_profile_cache()
    yield
    reset_organization_profile_cache()


def test_default_profile_uses_fictional_domains_and_products():
    profile = build_organization_profile()

    assert profile.name == "Example Company"
    assert profile.primary_domains == ("example.com",)
    assert profile.resource_domains == ("example.com",)
    assert get_product_patterns(profile)["microwave_radio"].search("RADIO-A100")
    assert get_url_product_patterns(profile)["mesh_radio"].search("/products/mesh-radio")


def test_custom_product_families_replace_default_vocabulary():
    profile = build_organization_profile({
        "product_families": {
            "edge_gateway": {
                "display_name": "edge gateway",
                "aliases": ["Gateway One"],
                "patterns": [r"GW-\d{3}"],
                "url_patterns": [r"edge[-_]gateway"],
            },
        },
    })

    patterns = get_product_patterns(profile)
    assert set(patterns) == {"edge_gateway"}
    assert patterns["edge_gateway"].search("Quote for GW-200")
    assert not patterns["edge_gateway"].search("RADIO-A100")


def test_domain_matching_requires_exact_domain_boundary():
    profile = build_organization_profile({"primary_domains": ["vendor.example.com"]})

    assert profile.is_first_party_host("vendor.example.com")
    assert profile.is_first_party_host("www.vendor.example.com")
    assert profile.is_first_party_host("WWW.VENDOR.EXAMPLE.COM:443")
    assert not profile.is_first_party_host("notvendor.example.com")
    assert not profile.is_first_party_host("vendor.example.com.external.example.net")


def test_resource_urls_use_separate_allowlist():
    profile = build_organization_profile({
        "primary_domains": ["vendor.example.com"],
        "resource_domains": ["assets.vendor.example.com", "cdn.example.net"],
    })

    assert profile.is_resource_url("https://assets.vendor.example.com/brief.pdf")
    assert profile.is_resource_url("https://media.cdn.example.net/brief.pdf")
    assert not profile.is_resource_url("https://vendor.example.com/brief.pdf")
    assert not profile.is_resource_url("https://cdn.example.net.evil.test/brief.pdf")


def test_environment_overrides_configured_profile(monkeypatch):
    monkeypatch.setenv("OUTREACH_ORGANIZATION_NAME", "Sample Networks")
    monkeypatch.setenv("OUTREACH_PRIMARY_DOMAINS", "sample.example.com, sample.example.net")
    monkeypatch.setenv("OUTREACH_PRODUCT_FAMILIES_JSON", json.dumps({
        "access_point": {
            "display_name": "access point",
            "aliases": ["AP One"],
            "patterns": [r"AP-\d+"],
            "url_patterns": [r"access[-_]point"],
        },
    }))

    profile = get_organization_profile()
    assert profile.name == "Sample Networks"
    assert profile.primary_domains == ("sample.example.com", "sample.example.net")
    assert [family.key for family in profile.product_families] == ["access_point"]


def test_invalid_product_environment_json_has_clear_error(monkeypatch):
    monkeypatch.setenv("OUTREACH_PRODUCT_FAMILIES_JSON", "not-json")

    with pytest.raises(ValueError, match="must be valid JSON"):
        get_organization_profile()
