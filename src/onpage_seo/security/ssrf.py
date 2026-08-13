"""SSRF guard for untrusted URL input (webhook/API/CLI with --ssrf-guard)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse


class SsrfBlockedError(ValueError):
    """Raised when a URL resolves to a disallowed address."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_url_safe(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SsrfBlockedError(f"unsupported scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise SsrfBlockedError("missing hostname")
    if host.lower() in {"localhost"} or host.endswith(".localhost"):
        raise SsrfBlockedError(f"blocked host: {host}")

    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SsrfBlockedError(f"DNS resolution failed for {host}: {exc}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(ip):
            raise SsrfBlockedError(f"blocked address {ip} for host {host}")


def resolve_redirect_url(current_url: str, location: str) -> str:
    if not location or not location.strip():
        raise SsrfBlockedError("empty redirect Location")
    return urljoin(current_url, location.strip())
