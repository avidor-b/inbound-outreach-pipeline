"""Organization-specific settings used by the reusable outreach pipeline.

The open-source defaults are intentionally fictional. Deployments can replace
them with an ``organization`` section in ``config.yaml`` or with the
``OUTREACH_*`` environment variables documented below. Keeping this data in
one module prevents product names and first-party domains from leaking into
classification, scoring, and enrichment logic.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Pattern, Sequence
from urllib.parse import urlsplit

from outreach_pipeline.config import ConfigError, load_config


@dataclass(frozen=True)
class ProductFamily:
    """Names and patterns that identify one product family."""

    key: str
    display_name: str
    aliases: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    url_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrganizationProfile:
    """Brand, domain, product, and market settings for one deployment."""

    name: str
    description: str
    primary_domains: tuple[str, ...]
    resource_domains: tuple[str, ...]
    product_families: tuple[ProductFamily, ...]
    relevance_terms: tuple[str, ...]
    company_fit_guidance: str

    @property
    def product_summary(self) -> str:
        names = [family.display_name for family in self.product_families]
        return ", ".join(names) if names else "products and services"

    def is_first_party_host(self, host: str) -> bool:
        """Return whether ``host`` is a configured domain or its subdomain."""
        normalized = _normalize_host(host)
        return any(
            normalized == domain or normalized.endswith(f".{domain}")
            for domain in self.primary_domains
        )

    def is_resource_url(self, url: str) -> bool:
        """Return whether a resource URL belongs to an approved domain."""
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            return False
        normalized = _normalize_host(host)
        return any(
            normalized == domain or normalized.endswith(f".{domain}")
            for domain in self.resource_domains
        )


_DEFAULT_PRODUCT_FAMILIES = (
    ProductFamily(
        key="mesh_radio",
        display_name="mesh radio",
        aliases=("mesh radio", "mesh node", "mesh base"),
        patterns=(r"MESH-(?:NODE|BASE)(?:-[A-Z0-9]+)?",),
        url_patterns=(r"mesh[-_]?radio", r"mesh[-_]?(?:node|base)"),
    ),
    ProductFamily(
        key="microwave_radio",
        display_name="microwave radio",
        aliases=("microwave radio", "RADIO-A"),
        patterns=(r"RADIO-A[A-Z0-9-]*",),
        url_patterns=(r"radio[-_]?a", r"microwave[-_]?radio"),
    ),
    ProductFamily(
        key="millimeter_wave_radio",
        display_name="millimeter-wave radio",
        aliases=("millimeter-wave radio", "millimetre-wave radio", "mmWave radio", "RADIO-B"),
        patterns=(r"RADIO-B[A-Z0-9-]*",),
        url_patterns=(r"radio[-_]?b", r"milli(?:meter|metre)[-_]?wave[-_]?radio"),
    ),
    ProductFamily(
        key="point_to_point_radio",
        display_name="point-to-point radio",
        aliases=("point-to-point radio", "point to point radio", "PtP radio", "RADIO-C"),
        patterns=(r"RADIO-C[A-Z0-9-]*",),
        url_patterns=(r"radio[-_]?c", r"point[-_]?to[-_]?point[-_]?radio"),
    ),
    ProductFamily(
        key="split_mount_radio",
        display_name="split-mount radio",
        aliases=("split-mount radio", "split mount radio", "RADIO-D"),
        patterns=(r"RADIO-D[A-Z0-9-]*",),
        url_patterns=(r"radio[-_]?d", r"split[-_]?mount[-_]?radio"),
    ),
    ProductFamily(
        key="outdoor_radio",
        display_name="outdoor radio",
        aliases=("outdoor radio", "all-outdoor radio", "RADIO-F"),
        patterns=(r"RADIO-F[A-Z0-9-]*",),
        url_patterns=(r"radio[-_]?f", r"(?:all[-_]?)?outdoor[-_]?radio"),
    ),
)

_DEFAULT_RELEVANCE_TERMS = (
    "wireless", "wifi", "wi-fi", "microwave", "mmwave", "mm-wave",
    "backhaul", "fronthaul", "antenna", "antennas", "radio", "radios",
    "link", "links", "transport", "bandwidth", "frequency", "5g", "4g",
    "lte", "base station", "tower", "cell site", "point-to-point", "ptp",
    "p2p", "p2mp", "mesh", "fiber", "fibre", "ethernet", "network",
    "networking", "telecom", "isp", "operator", "carrier", "connectivity",
    "throughput", "capacity", "spectrum", "line of sight", "nlos", "los",
)

_DEFAULT_COMPANY_FIT_GUIDANCE = """\
   0 = unknown or unenriched. 2 = clearly unrelated at any size (for
     example, a consumer service or non-technical professional firm).
   4 = tangential enterprise technology or a small organization with no
     distributed footprint.
   6 = an adjacent, multi-site vertical such as healthcare, education,
     manufacturing, venues, hospitality, retail, logistics, or aviation.
   7 = public sector, public safety, transportation, defense, or a municipal
     infrastructure program.
   8 = mining, energy, ports, utilities, private wireless deployments, or a
     distributor or reseller of networking hardware.
   10 = wireless connectivity is the organization's core business: an ISP,
     WISP, mobile operator, carrier, neutral host, or telecom integrator.
   If the stated use case is private LTE or private 5G, score company_fit at
   least 8 even when the company is not otherwise wireless-native."""


def _default_profile() -> OrganizationProfile:
    return OrganizationProfile(
        name="Example Company",
        description="a provider of wireless transport and private-network infrastructure",
        primary_domains=("example.com",),
        resource_domains=("example.com",),
        product_families=_DEFAULT_PRODUCT_FAMILIES,
        relevance_terms=_DEFAULT_RELEVANCE_TERMS,
        company_fit_guidance=_DEFAULT_COMPANY_FIT_GUIDANCE,
    )


def _as_string_tuple(value: Any, default: Sequence[str] = ()) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, Sequence):
        values = value
    else:
        return tuple(default)
    return tuple(str(item).strip() for item in values if str(item).strip())


def _normalize_host(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    if "://" in host:
        try:
            host = urlsplit(host).hostname or ""
        except ValueError:
            return ""
    elif ":" in host:
        try:
            host = urlsplit(f"//{host}").hostname or ""
        except ValueError:
            return ""
    return host.lstrip(".").rstrip(".")


def _normalize_domains(value: Any, default: Sequence[str]) -> tuple[str, ...]:
    domains: list[str] = []
    for item in _as_string_tuple(value, default):
        host = _normalize_host(item)
        if host and host not in domains:
            domains.append(host)
    return tuple(domains)


def _product_families_from_mapping(value: Any) -> tuple[ProductFamily, ...]:
    if not isinstance(value, Mapping):
        return _DEFAULT_PRODUCT_FAMILIES

    families: list[ProductFamily] = []
    for key, raw in value.items():
        if not isinstance(raw, Mapping):
            continue
        family_key = str(key).strip()
        if not family_key:
            continue
        display_name = str(raw.get("display_name") or family_key.replace("_", " ")).strip()
        families.append(ProductFamily(
            key=family_key,
            display_name=display_name,
            aliases=_as_string_tuple(raw.get("aliases"), (display_name,)),
            patterns=_as_string_tuple(raw.get("patterns")),
            url_patterns=_as_string_tuple(raw.get("url_patterns")),
        ))
    return tuple(families) or _DEFAULT_PRODUCT_FAMILIES


def build_organization_profile(settings: Mapping[str, Any] | None = None) -> OrganizationProfile:
    """Build a validated profile from config-shaped settings."""
    base = _default_profile()
    values = dict(settings or {})
    primary_domains = _normalize_domains(values.get("primary_domains"), base.primary_domains)
    resource_domains = _normalize_domains(
        values.get("resource_domains"), primary_domains or base.resource_domains,
    )
    return OrganizationProfile(
        name=str(values.get("name") or base.name).strip(),
        description=str(values.get("description") or base.description).strip(),
        primary_domains=primary_domains,
        resource_domains=resource_domains,
        product_families=_product_families_from_mapping(values.get("product_families")),
        relevance_terms=_as_string_tuple(values.get("relevance_terms"), base.relevance_terms),
        company_fit_guidance=str(
            values.get("company_fit_guidance") or base.company_fit_guidance
        ).strip(),
    )


def _environment_settings() -> dict[str, Any]:
    settings: dict[str, Any] = {}
    simple = {
        "OUTREACH_ORGANIZATION_NAME": "name",
        "OUTREACH_ORGANIZATION_DESCRIPTION": "description",
        "OUTREACH_PRIMARY_DOMAINS": "primary_domains",
        "OUTREACH_RESOURCE_DOMAINS": "resource_domains",
        "OUTREACH_RELEVANCE_TERMS": "relevance_terms",
        "OUTREACH_COMPANY_FIT_GUIDANCE": "company_fit_guidance",
    }
    for env_name, key in simple.items():
        if env_name in os.environ:
            settings[key] = os.environ[env_name]

    raw_products = os.environ.get("OUTREACH_PRODUCT_FAMILIES_JSON")
    if raw_products:
        try:
            products = json.loads(raw_products)
        except json.JSONDecodeError as exc:
            raise ValueError("OUTREACH_PRODUCT_FAMILIES_JSON must be valid JSON") from exc
        if not isinstance(products, Mapping):
            raise ValueError("OUTREACH_PRODUCT_FAMILIES_JSON must be a JSON object")
        settings["product_families"] = products
    return settings


@lru_cache(maxsize=1)
def get_organization_profile() -> OrganizationProfile:
    """Load the deployment profile, with environment variables taking precedence."""
    settings: dict[str, Any] = {}
    try:
        config = load_config()
    except ConfigError:
        config = {}
    configured = config.get("organization", {}) if isinstance(config, Mapping) else {}
    if isinstance(configured, Mapping):
        settings.update(configured)
    settings.update(_environment_settings())
    return build_organization_profile(settings)


def reset_organization_profile_cache() -> None:
    """Clear cached settings after changing config or environment in tests."""
    get_organization_profile.cache_clear()


def _compile_terms(terms: Sequence[str], *, raw: bool = False) -> Pattern[str]:
    parts = []
    for term in terms:
        part = term if raw else re.escape(term).replace(r"\ ", r"\s+")
        if part:
            parts.append(part)
    if not parts:
        return re.compile(r"(?!x)x")
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


def get_product_patterns(profile: OrganizationProfile | None = None) -> dict[str, Pattern[str]]:
    profile = profile or get_organization_profile()
    patterns: dict[str, Pattern[str]] = {}
    for family in profile.product_families:
        escaped_aliases = [re.escape(alias).replace(r"\ ", r"\s+") for alias in family.aliases]
        patterns[family.key] = _compile_terms([*escaped_aliases, *family.patterns], raw=True)
    return patterns


def get_url_product_patterns(profile: OrganizationProfile | None = None) -> dict[str, Pattern[str]]:
    profile = profile or get_organization_profile()
    return {
        family.key: _compile_terms(family.url_patterns, raw=True)
        for family in profile.product_families
    }


def get_relevance_pattern(profile: OrganizationProfile | None = None) -> Pattern[str]:
    profile = profile or get_organization_profile()
    return _compile_terms(profile.relevance_terms)


def known_product_mentioned(text: str, profile: OrganizationProfile | None = None) -> bool:
    return any(pattern.search(text or "") for pattern in get_product_patterns(profile).values())
