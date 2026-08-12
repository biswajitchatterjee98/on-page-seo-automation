"""Versioned deterministic on-page SEO checks."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from onpage_seo.config import Thresholds

RULES_VERSION = "1.0.0"


def _result(
    check_id: str,
    status: str,
    weight: int,
    detail: str,
    *,
    warn_ratio: float,
) -> dict[str, Any]:
    if status == "pass":
        score = weight
    elif status == "warn":
        score = int(round(weight * warn_ratio))
    elif status == "skip":
        score = weight  # neutral: do not punish missing optional context
    else:
        score = 0
    return {
        "id": check_id,
        "status": status,
        "weight": weight,
        "score": score,
        "detail": detail,
    }


def _contains_keyword(text: str, keyword: str) -> bool:
    if not keyword.strip():
        return False
    return keyword.strip().lower() in (text or "").lower()


def _slug(url: str) -> str:
    path = urlparse(url).path or ""
    return path.strip("/").lower()


def evaluate_page(
    page: dict[str, Any],
    keywords: list[str],
    thresholds: Thresholds,
    *,
    duplicate_titles: set[str] | None = None,
    duplicate_metas: set[str] | None = None,
    competitor_avg_word_count: float | None = None,
) -> list[dict[str, Any]]:
    weights = thresholds.weights
    warn_ratio = thresholds.warn_score_ratio
    primary = keywords[0] if keywords else ""
    title = page.get("title") or ""
    meta = page.get("meta_description") or ""
    headers = page.get("headers") or {}
    h1_list = headers.get("h1") or []
    body = page.get("body_text") or ""
    word_count = int(page.get("word_count") or 0)
    images = page.get("images") or []
    schema = page.get("schema") or []
    url = page.get("final_url") or page.get("url") or ""
    density = float((page.get("keyword_density") or {}).get("primary") or 0.0)

    checks: list[dict[str, Any]] = []

    # title_length
    w = weights.get("title_length", 10)
    if not title:
        checks.append(_result("title_length", "fail", w, "title is empty", warn_ratio=warn_ratio))
    elif thresholds.title_min <= len(title) <= thresholds.title_max:
        checks.append(
            _result(
                "title_length",
                "pass",
                w,
                f"title length {len(title)} within {thresholds.title_min}-{thresholds.title_max}",
                warn_ratio=warn_ratio,
            )
        )
    else:
        checks.append(
            _result(
                "title_length",
                "warn",
                w,
                f"title length {len(title)} outside {thresholds.title_min}-{thresholds.title_max}",
                warn_ratio=warn_ratio,
            )
        )

    # meta_description_length
    w = weights.get("meta_description_length", 10)
    if not meta:
        checks.append(
            _result("meta_description_length", "fail", w, "meta description is empty", warn_ratio=warn_ratio)
        )
    elif thresholds.meta_min <= len(meta) <= thresholds.meta_max:
        checks.append(
            _result(
                "meta_description_length",
                "pass",
                w,
                f"meta length {len(meta)} within {thresholds.meta_min}-{thresholds.meta_max}",
                warn_ratio=warn_ratio,
            )
        )
    else:
        checks.append(
            _result(
                "meta_description_length",
                "warn",
                w,
                f"meta length {len(meta)} outside {thresholds.meta_min}-{thresholds.meta_max}",
                warn_ratio=warn_ratio,
            )
        )

    # keyword checks
    def keyword_check(check_id: str, weight_key: str, haystack: str, required: bool) -> None:
        weight = weights.get(weight_key, 0)
        if not primary:
            checks.append(
                _result(check_id, "skip", weight, "no keyword provided", warn_ratio=warn_ratio)
            )
            return
        if _contains_keyword(haystack, primary):
            checks.append(
                _result(check_id, "pass", weight, f"keyword {primary!r} found", warn_ratio=warn_ratio)
            )
        elif required:
            checks.append(
                _result(check_id, "fail", weight, f"keyword {primary!r} missing", warn_ratio=warn_ratio)
            )
        else:
            checks.append(
                _result(check_id, "warn", weight, f"keyword {primary!r} missing", warn_ratio=warn_ratio)
            )

    keyword_check("keyword_in_title", "keyword_in_title", title, required=True)
    keyword_check("keyword_in_h1", "keyword_in_h1", " ".join(h1_list), required=True)

    intro_words = re.findall(r"[A-Za-z0-9']+", body.lower())[: thresholds.intro_word_count]
    intro = " ".join(intro_words)
    keyword_check("keyword_in_intro", "keyword_in_intro", intro, required=True)
    keyword_check("keyword_in_url", "keyword_in_url", _slug(url).replace("-", " "), required=False)

    # keyword_density
    w = weights.get("keyword_density", 8)
    if not primary:
        checks.append(_result("keyword_density", "skip", w, "no keyword provided", warn_ratio=warn_ratio))
    elif thresholds.keyword_density_min_pct <= density <= thresholds.keyword_density_max_pct:
        checks.append(
            _result(
                "keyword_density",
                "pass",
                w,
                f"density {density}% within {thresholds.keyword_density_min_pct}-{thresholds.keyword_density_max_pct}%",
                warn_ratio=warn_ratio,
            )
        )
    else:
        checks.append(
            _result(
                "keyword_density",
                "warn",
                w,
                f"density {density}% outside {thresholds.keyword_density_min_pct}-{thresholds.keyword_density_max_pct}%",
                warn_ratio=warn_ratio,
            )
        )

    # image_alt
    w = weights.get("image_alt", 10)
    missing = [img["src"] for img in images if not str(img.get("alt", "")).strip()]
    if not images:
        checks.append(_result("image_alt", "pass", w, "no images on page", warn_ratio=warn_ratio))
    elif missing:
        checks.append(
            _result(
                "image_alt",
                "fail",
                w,
                f"{len(missing)} image(s) missing alt text",
                warn_ratio=warn_ratio,
            )
        )
    else:
        checks.append(_result("image_alt", "pass", w, "all images have alt text", warn_ratio=warn_ratio))

    # heading_hierarchy
    w = weights.get("heading_hierarchy", 8)
    issues: list[str] = []
    if len(h1_list) == 0:
        issues.append("missing H1")
    elif len(h1_list) > 1:
        issues.append(f"multiple H1 ({len(h1_list)})")
    last_level = 0
    for level in range(1, 7):
        if headers.get(f"h{level}"):
            if last_level and level > last_level + 1:
                issues.append(f"skipped to H{level} after H{last_level}")
            last_level = level
    if issues:
        checks.append(
            _result("heading_hierarchy", "warn", w, "; ".join(issues), warn_ratio=warn_ratio)
        )
    else:
        checks.append(_result("heading_hierarchy", "pass", w, "heading hierarchy looks sound", warn_ratio=warn_ratio))

    # thin_content
    w = weights.get("thin_content", 8)
    if word_count < thresholds.min_word_count:
        checks.append(
            _result(
                "thin_content",
                "fail",
                w,
                f"word_count {word_count} < min {thresholds.min_word_count}",
                warn_ratio=warn_ratio,
            )
        )
    else:
        checks.append(
            _result(
                "thin_content",
                "pass",
                w,
                f"word_count {word_count} >= {thresholds.min_word_count}",
                warn_ratio=warn_ratio,
            )
        )

    # competitor_length
    w = weights.get("competitor_length", 5)
    if competitor_avg_word_count is None:
        checks.append(
            _result("competitor_length", "skip", w, "no competitor sample", warn_ratio=warn_ratio)
        )
    else:
        floor = competitor_avg_word_count * thresholds.competitor_length_factor
        if word_count >= floor:
            checks.append(
                _result(
                    "competitor_length",
                    "pass",
                    w,
                    f"word_count {word_count} >= {floor:.0f} (competitor factor)",
                    warn_ratio=warn_ratio,
                )
            )
        else:
            checks.append(
                _result(
                    "competitor_length",
                    "warn",
                    w,
                    f"word_count {word_count} < {floor:.0f} vs competitors",
                    warn_ratio=warn_ratio,
                )
            )

    # schema_presence
    w = weights.get("schema_presence", 4)
    if not thresholds.expect_schema:
        checks.append(
            _result("schema_presence", "skip", w, "expect_schema=false", warn_ratio=warn_ratio)
        )
    elif schema:
        checks.append(_result("schema_presence", "pass", w, f"{len(schema)} schema block(s)", warn_ratio=warn_ratio))
    else:
        checks.append(_result("schema_presence", "warn", w, "no JSON-LD schema found", warn_ratio=warn_ratio))

    # duplicate_title_meta
    w = weights.get("duplicate_title_meta", 4)
    if duplicate_titles is None and duplicate_metas is None:
        checks.append(
            _result("duplicate_title_meta", "skip", w, "single-page job", warn_ratio=warn_ratio)
        )
    else:
        dups = []
        if title and duplicate_titles and title in duplicate_titles:
            dups.append("duplicate title")
        if meta and duplicate_metas and meta in duplicate_metas:
            dups.append("duplicate meta")
        if dups:
            checks.append(_result("duplicate_title_meta", "fail", w, ", ".join(dups), warn_ratio=warn_ratio))
        else:
            checks.append(
                _result("duplicate_title_meta", "pass", w, "title/meta unique in job", warn_ratio=warn_ratio)
            )

    return checks


def score_checks(checks: list[dict[str, Any]]) -> tuple[int, int]:
    overall = sum(int(item["score"]) for item in checks)
    maximum = sum(int(item["weight"]) for item in checks)
    return overall, maximum
