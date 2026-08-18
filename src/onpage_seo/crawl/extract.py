"""HTML → page JSON extraction."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString

_SKIP_TEXT = {"script", "style", "noscript", "template", "nav", "footer", "header", "aside"}


def _visible_text(soup: BeautifulSoup) -> str:
    root = soup.body or soup
    chunks: list[str] = []
    for element in root.descendants:
        if isinstance(element, NavigableString):
            parent = element.parent
            node = parent
            skipped = False
            while node is not None:
                if getattr(node, "name", None) in _SKIP_TEXT:
                    skipped = True
                    break
                node = getattr(node, "parent", None)
            if skipped:
                continue
            text = str(element).strip()
            if text:
                chunks.append(text)
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def keyword_density_pct(body_text: str, keyword: str) -> float:
    words = _words(body_text)
    if not words or not keyword.strip():
        return 0.0
    needle = keyword.strip().lower()
    needle_words = _words(needle)
    if not needle_words:
        return 0.0
    if len(needle_words) == 1:
        count = sum(1 for word in words if word == needle_words[0])
    else:
        # ponytail: O(n) sliding window for multi-word keywords; fine for page-sized text
        count = 0
        width = len(needle_words)
        for index in range(len(words) - width + 1):
            if words[index : index + width] == needle_words:
                count += 1
    return round(100.0 * count / len(words), 4)


def extract_page(
    html: str,
    *,
    url: str,
    final_url: str,
    http_status: int,
    render_mode: str,
    primary_keyword: str | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""
    meta_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    meta_description = ""
    if meta_tag and meta_tag.get("content"):
        meta_description = str(meta_tag["content"]).strip()

    canonical = ""
    link_canonical = soup.find("link", attrs={"rel": re.compile(r"\bcanonical\b", re.I)})
    if link_canonical and link_canonical.get("href"):
        canonical = urljoin(final_url, str(link_canonical["href"]))

    headers: dict[str, list[str]] = {f"h{level}": [] for level in range(1, 7)}
    for level in range(1, 7):
        headers[f"h{level}"] = [
            tag.get_text(" ", strip=True) for tag in soup.find_all(f"h{level}")
        ]

    body_text = _visible_text(soup)
    words = _words(body_text)
    word_count = len(words)

    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        images.append(
            {
                "src": urljoin(final_url, str(src)),
                "alt": "" if img.get("alt") is None else str(img.get("alt")),
            }
        )

    parsed_host = urlparse(final_url)
    base_netloc = parsed_host.netloc.lower()
    internal: list[str] = []
    external: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(final_url, href)
        netloc = urlparse(absolute).netloc.lower()
        if netloc == base_netloc:
            internal.append(absolute)
        elif netloc:
            external.append(absolute)

    schema: list[Any] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            schema.append(json.loads(raw))
        except json.JSONDecodeError:
            schema.append({"_invalid_json": True, "raw_preview": raw[:120]})

    density = {"primary": 0.0}
    if primary_keyword:
        density["primary"] = keyword_density_pct(body_text, primary_keyword)

    return {
        "url": url,
        "final_url": final_url,
        "http_status": http_status,
        "title": title,
        "meta_description": meta_description,
        "canonical": canonical,
        "headers": headers,
        "body_text": body_text,
        "word_count": word_count,
        "keyword_density": density,
        "images": images,
        "links": {
            "internal": sorted(set(internal)),
            "external": sorted(set(external)),
        },
        "schema": schema,
        "render_mode": render_mode,
        "robots_allowed": True,
    }
