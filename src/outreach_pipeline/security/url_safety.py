"""Outbound URL validation for SSRF-sensitive fetch and scrape tools."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    """Raised when an outbound URL targets a disallowed network location."""


_ALLOWED_SCHEMES = {"http", "https"}
_LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain"}


def _resolve_host(hostname: str) -> list[ipaddress._BaseAddress]:
    """Resolve a hostname and return parsed IP addresses.

    All resolved addresses must be public. Allowing a hostname when only one
    address is public is unsafe because the client may pick another address.
    """
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURL(f"Cannot resolve host: {hostname}") from exc

    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addresses.append(ipaddress.ip_address(sockaddr[0]))
    if not addresses:
        raise UnsafeURL(f"Cannot resolve host: {hostname}")
    return addresses


def _assert_public_ip(address: ipaddress._BaseAddress) -> None:
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise UnsafeURL(f"Blocked non-public IP target: {address}")


def assert_public_http_url(url: str) -> str:
    """Validate that ``url`` is an absolute HTTP(S) URL to public IP space.

    Call before outbound requests and again on the final URL after redirects.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeURL("Only http and https URLs are allowed")
    if not parsed.hostname:
        raise UnsafeURL("URL must include a hostname")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in _LOCAL_HOSTNAMES:
        raise UnsafeURL("Localhost targets are not allowed")

    try:
        _assert_public_ip(ipaddress.ip_address(hostname))
        return url
    except ValueError:
        pass

    for address in _resolve_host(hostname):
        _assert_public_ip(address)
    return url

