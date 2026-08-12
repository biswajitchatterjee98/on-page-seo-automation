"""Re-validate LLM drafts against rule thresholds before they can be queued."""

from __future__ import annotations

from typing import Any

from onpage_seo.config import Thresholds


def validate_title(title: str, thresholds: Thresholds) -> tuple[bool, str]:
    if not title or not title.strip():
        return False, "title is empty"
    length = len(title.strip())
    if length < thresholds.title_min or length > thresholds.title_max:
        return False, f"title length {length} outside {thresholds.title_min}-{thresholds.title_max}"
    return True, "ok"


def validate_meta(meta: str, thresholds: Thresholds) -> tuple[bool, str]:
    if not meta or not meta.strip():
        return False, "meta description is empty"
    length = len(meta.strip())
    if length < thresholds.meta_min or length > thresholds.meta_max:
        return False, f"meta length {length} outside {thresholds.meta_min}-{thresholds.meta_max}"
    return True, "ok"


def validate_alt(alt: str) -> tuple[bool, str]:
    if not alt or not alt.strip():
        return False, "alt text is empty"
    if len(alt.strip()) > 125:
        return False, "alt text longer than 125 characters"
    return True, "ok"


def partition_suggestions(
    raw: dict[str, Any],
    thresholds: Thresholds,
) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
    accepted: dict[str, list[Any]] = {
        "title": [],
        "meta_description": [],
        "alt_text": [],
        "internal_links": list(raw.get("internal_links") or []),
    }
    rejected: list[dict[str, Any]] = []

    for title in raw.get("title") or []:
        ok, reason = validate_title(str(title), thresholds)
        if ok:
            accepted["title"].append(title)
        else:
            rejected.append({"field": "title", "value": title, "reason": reason})

    for meta in raw.get("meta_description") or []:
        ok, reason = validate_meta(str(meta), thresholds)
        if ok:
            accepted["meta_description"].append(meta)
        else:
            rejected.append({"field": "meta_description", "value": meta, "reason": reason})

    for item in raw.get("alt_text") or []:
        if isinstance(item, dict):
            alt = str(item.get("suggested_alt") or "")
            ok, reason = validate_alt(alt)
            if ok:
                accepted["alt_text"].append(item)
            else:
                rejected.append({"field": "alt_text", "value": item, "reason": reason})
        else:
            rejected.append({"field": "alt_text", "value": item, "reason": "expected object with suggested_alt"})

    return accepted, rejected
