"""LLM semantic coverage + suggestion generation (rules still own the score)."""

from __future__ import annotations

import json
import logging
from typing import Any

from onpage_seo.config import Settings, Thresholds
from onpage_seo.embeddings import suggest_internal_links
from onpage_seo.llm.client import PROMPT_VERSION, LlmBudget, LlmClient
from onpage_seo.readability import flesch_reading_ease
from onpage_seo.validate import partition_suggestions

logger = logging.getLogger("onpage_seo")

_SYSTEM = (
    f"You are an on-page SEO assistant ({PROMPT_VERSION}). "
    "Return ONLY valid JSON. Do not invent facts not supported by the page text. "
    "Title suggestions must be 50-60 characters. Meta descriptions must be 150-160 characters."
)


def build_client(settings: Settings) -> LlmClient | None:
    if not settings.llm_enabled or not settings.openai_api_key:
        return None
    return LlmClient(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        max_tokens=settings.llm_max_tokens,
        timeout_sec=settings.timeout_sec,
        budget=LlmBudget(max_calls=settings.llm_max_calls_per_job),
    )


def enrich_report_with_llm(
    report: dict[str, Any],
    page: dict[str, Any],
    *,
    keywords: list[str],
    thresholds: Thresholds,
    settings: Settings,
    client: LlmClient | None,
    corpus: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mutate/return report with llm block. Never raises — degrades to error/skipped."""
    if report.get("status") == "error":
        return report

    readability = flesch_reading_ease(str(page.get("body_text") or ""))
    notes = [f"Flesch reading ease: {readability}"]

    internal_links: list[dict[str, str]] = []
    corpus = corpus or []
    if len([p for p in corpus if p.get("status") != "error"]) >= settings.internal_link_min_pages:
        internal_links = suggest_internal_links(page, corpus)

    if client is None:
        report["llm"] = {
            "status": "skipped",
            "semantic_coverage_score": 0,
            "notes": notes + ["LLM disabled or API key missing"],
            "prompt_version": PROMPT_VERSION,
            "suggestions": {
                "title": [],
                "meta_description": [],
                "alt_text": [],
                "internal_links": internal_links,
            },
            "rejected_suggestions": [],
        }
        report["model_version"] = None
        return report

    missing_alts = [
        {"src": img.get("src"), "current_alt": img.get("alt")}
        for img in page.get("images") or []
        if not str(img.get("alt") or "").strip()
    ][:5]

    user_payload = {
        "url": page.get("final_url") or page.get("url"),
        "keywords": keywords,
        "title": page.get("title"),
        "meta_description": page.get("meta_description"),
        "h1": (page.get("headers") or {}).get("h1"),
        "body_excerpt": str(page.get("body_text") or "")[:4000],
        "missing_alt_images": missing_alts,
        "flesch_reading_ease": readability,
        "title_length_target": [thresholds.title_min, thresholds.title_max],
        "meta_length_target": [thresholds.meta_min, thresholds.meta_max],
    }

    try:
        raw = client.chat_json(
            system=_SYSTEM,
            user=(
                "Analyze on-page SEO for this page and return JSON with keys: "
                "semantic_coverage_score (0-100), notes (string array), "
                "title (string array of 1-3 suggestions), "
                "meta_description (string array of 1-3 suggestions), "
                "alt_text (array of {src, suggested_alt}).\n"
                + json.dumps(user_payload, ensure_ascii=False)
            ),
        )
        accepted, rejected = partition_suggestions(
            {
                "title": raw.get("title") or [],
                "meta_description": raw.get("meta_description") or [],
                "alt_text": raw.get("alt_text") or [],
                "internal_links": internal_links,
            },
            thresholds,
        )
        llm_notes = notes + [str(n) for n in (raw.get("notes") or [])][:8]
        report["llm"] = {
            "status": "ok",
            "semantic_coverage_score": int(raw.get("semantic_coverage_score") or 0),
            "notes": llm_notes,
            "prompt_version": PROMPT_VERSION,
            "suggestions": accepted,
            "rejected_suggestions": rejected,
        }
        report["model_version"] = client.model_version
    except Exception as exc:  # noqa: BLE001 — degrade; rules report remains
        logger.info(
            "llm failed",
            extra={
                "job_id": report.get("job_id"),
                "url": report.get("url"),
                "stage": "llm",
                "status": "error",
                "error_code": "llm_error",
            },
        )
        report["llm"] = {
            "status": "error",
            "semantic_coverage_score": 0,
            "notes": notes + [f"LLM error: {exc}"],
            "prompt_version": PROMPT_VERSION,
            "suggestions": {
                "title": [],
                "meta_description": [],
                "alt_text": [],
                "internal_links": internal_links,
            },
            "rejected_suggestions": [],
        }
        report["model_version"] = client.model_version
    return report
