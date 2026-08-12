"""Resolve audit targets from URL lists and sitemaps."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests

from onpage_seo.config import Settings
from onpage_seo.security.ssrf import SsrfBlockedError, assert_url_safe


def load_url_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_sitemap_xml(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemap_urls) from a sitemap or sitemap index."""
    root = ET.fromstring(xml_text)
    locs = [
        (node.text or "").strip()
        for node in root.iter()
        if _local_tag(node.tag) == "loc" and (node.text or "").strip()
    ]
    if _local_tag(root.tag) == "sitemapindex":
        return [], locs
    return locs, []


def fetch_sitemap_urls(
    sitemap_url: str,
    settings: Settings,
    *,
    max_pages: int,
    enforce_ssrf: bool = True,
) -> list[str]:
    if enforce_ssrf:
        assert_url_safe(sitemap_url)

    response = requests.get(
        sitemap_url,
        timeout=settings.timeout_sec,
        headers={"User-Agent": settings.user_agent},
    )
    response.raise_for_status()
    pages, children = parse_sitemap_xml(response.text)
    collected = list(pages)

    # ponytail: one-level sitemap index only; upgrade: BFS with visited set
    for child in children:
        if len(collected) >= max_pages:
            break
        if enforce_ssrf:
            try:
                assert_url_safe(child)
            except SsrfBlockedError:
                continue
        try:
            child_resp = requests.get(
                child,
                timeout=settings.timeout_sec,
                headers={"User-Agent": settings.user_agent},
            )
            child_resp.raise_for_status()
            child_pages, _ = parse_sitemap_xml(child_resp.text)
            collected.extend(child_pages)
        except requests.RequestException:
            continue

    return dedupe_cap(collected, max_pages)


def dedupe_cap(urls: Iterable[str], max_pages: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        cleaned = url.strip()
        if not cleaned or cleaned in seen:
            continue
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"}:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= max_pages:
            break
    return out


def resolve_targets(
    *,
    urls: list[str] | None = None,
    url_file: Path | None = None,
    sitemap_url: str | None = None,
    settings: Settings,
    max_pages: int,
    enforce_ssrf: bool = True,
) -> list[str]:
    collected: list[str] = []
    if urls:
        collected.extend(urls)
    if url_file is not None:
        collected.extend(load_url_file(url_file))
    if sitemap_url:
        collected.extend(
            fetch_sitemap_urls(
                sitemap_url,
                settings,
                max_pages=max_pages,
                enforce_ssrf=enforce_ssrf,
            )
        )
    return dedupe_cap(collected, max_pages)
