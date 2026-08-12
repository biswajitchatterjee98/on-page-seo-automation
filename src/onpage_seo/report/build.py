"""Combine crawl + rules into the report contract."""

from __future__ import annotations

from typing import Any

from onpage_seo.config import Thresholds
from onpage_seo.rules.engine import RULES_VERSION, evaluate_page, score_checks


def build_report(
    *,
    job_id: str,
    page: dict[str, Any],
    keywords: list[str],
    thresholds: Thresholds,
    competitor_avg_word_count: float | None = None,
) -> dict[str, Any]:
    if page.get("status") == "error":
        return {
            "job_id": job_id,
            "url": page.get("url"),
            "keywords": keywords,
            "rules_version": thresholds.rules_version or RULES_VERSION,
            "model_version": None,
            "overall_score": 0,
            "max_score": 100,
            "status": "error",
            "error_code": page.get("error_code"),
            "detail": page.get("detail"),
            "rules": [],
            "competitor": {
                "enabled": competitor_avg_word_count is not None,
                "target_word_count": 0,
                "competitor_avg_word_count": competitor_avg_word_count or 0,
                "delta": 0,
            },
            "llm": {
                "status": "skipped",
                "semantic_coverage_score": 0,
                "notes": [],
                "suggestions": {
                    "title": [],
                    "meta_description": [],
                    "alt_text": [],
                    "internal_links": [],
                },
                "rejected_suggestions": [],
            },
            "page": page,
        }

    checks = evaluate_page(
        page,
        keywords,
        thresholds,
        competitor_avg_word_count=competitor_avg_word_count,
    )
    overall, maximum = score_checks(checks)
    target_words = int(page.get("word_count") or 0)
    competitor_enabled = competitor_avg_word_count is not None
    avg = float(competitor_avg_word_count or 0)

    return {
        "job_id": job_id,
        "url": page.get("final_url") or page.get("url"),
        "keywords": keywords,
        "rules_version": thresholds.rules_version or RULES_VERSION,
        "model_version": None,
        "overall_score": overall,
        "max_score": maximum,
        "status": "ok",
        "rules": checks,
        "competitor": {
            "enabled": competitor_enabled,
            "target_word_count": target_words,
            "competitor_avg_word_count": avg,
            "delta": target_words - avg if competitor_enabled else 0,
        },
        "llm": {
            "status": "skipped",
            "semantic_coverage_score": 0,
            "notes": [],
            "suggestions": {
                "title": [],
                "meta_description": [],
                "alt_text": [],
                "internal_links": [],
            },
            "rejected_suggestions": [],
        },
        "page": {
            "title": page.get("title"),
            "meta_description": page.get("meta_description"),
            "word_count": page.get("word_count"),
            "keyword_density": page.get("keyword_density"),
            "render_mode": page.get("render_mode"),
            "http_status": page.get("http_status"),
            "canonical": page.get("canonical"),
            "headers": page.get("headers"),
            "images_count": len(page.get("images") or []),
            "schema_count": len(page.get("schema") or []),
        },
    }
