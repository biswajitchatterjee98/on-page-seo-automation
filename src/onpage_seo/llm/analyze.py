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


def _usable_api_key(key: str | None) -> bool:
    if not key:
        return False
    return key.strip().lower() not in {"none", "changeme", "gsk_your_groq_api_key"}


def build_client(settings: Settings) -> LlmClient | None:
    if not settings.llm_enabled or not _usable_api_key(settings.llm_api_key):
        return None
    return LlmClient(
        api_key=settings.llm_api_key or "",
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        timeout_sec=settings.timeout_sec,
        budget=LlmBudget(max_calls=settings.llm_max_calls_per_job),
    )


def _in_band_example(seed: str, minimum: int, maximum: int) -> str:
    """Whole-word example inside [minimum, maximum]. Never mid-word slice."""
    filler = [
        "for",
        "teams",
        "across",
        "India",
        "with",
        "clear",
        "goals",
        "and",
        "trusted",
        "delivery",
    ]
    parts = " ".join(seed.split()).split()
    text = " ".join(parts)
    index = 0
    while len(text) < minimum:
        parts.append(filler[index % len(filler)])
        index += 1
        text = " ".join(parts)
        if len(text) > maximum:
            parts.pop()
            text = " ".join(parts)
            break
    while len(text) > maximum and parts:
        parts.pop()
        text = " ".join(parts)
    if minimum <= len(text) <= maximum:
        return text
    for extra in (" 2026", " India", " now", " SEO"):
        candidate = text + extra
        if minimum <= len(candidate) <= maximum:
            return candidate
    return text


def _failed_rule_summaries(report: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for check in report.get("rules") or []:
        status = str(check.get("status") or "")
        if status not in {"fail", "warn"}:
            continue
        rows.append(
            {
                "id": str(check.get("id") or ""),
                "status": status,
                "detail": str(check.get("detail") or ""),
            }
        )
    return rows


def _system_prompt(thresholds: Thresholds) -> str:
    title_ex = _in_band_example(
        "Digital Transformation Consulting Services in India",
        thresholds.title_min,
        thresholds.title_max,
    )
    meta_ex = _in_band_example(
        "Webisdom helps organizations with digital transformation, web, and SEO programs that improve visibility and community impact.",
        thresholds.meta_min,
        thresholds.meta_max,
    )
    return (
        f"You are an on-page SEO assistant ({PROMPT_VERSION}). Return ONLY valid JSON. "
        "Do not invent facts not supported by the page text. "
        f"Each title MUST be {thresholds.title_min}-{thresholds.title_max} characters (count spaces). "
        f"Each meta_description MUST be {thresholds.meta_min}-{thresholds.meta_max} characters (count spaces). "
        "Every suggestion must end on a complete English word — never cut a word to hit the count. "
        "Count before you output. If a draft is outside the band, rewrite with whole words. "
        f"Example title ({len(title_ex)} chars): {title_ex!r}. "
        f"Example meta ({len(meta_ex)} chars): {meta_ex!r}. "
        "notes must agree with rule_results: never say a check passed or is in-range when status is fail or warn. "
        "Alt text must describe the image; never use filler like 'background image' or 'mid section image'."
    )


def _user_prompt(
    page: dict[str, Any],
    report: dict[str, Any],
    *,
    keywords: list[str],
    thresholds: Thresholds,
    readability: float,
    missing_alts: list[dict[str, Any]],
) -> str:
    title = str(page.get("title") or "")
    meta = str(page.get("meta_description") or "")
    payload = {
        "url": page.get("final_url") or page.get("url"),
        "keywords": keywords,
        "title": title,
        "title_chars": len(title),
        "meta_description": meta,
        "meta_chars": len(meta),
        "h1": (page.get("headers") or {}).get("h1"),
        "body_excerpt": str(page.get("body_text") or "")[:4000],
        "missing_alt_images": missing_alts,
        "flesch_reading_ease": readability,
        "title_length_target": [thresholds.title_min, thresholds.title_max],
        "meta_length_target": [thresholds.meta_min, thresholds.meta_max],
        "overall_score": report.get("overall_score"),
        "max_score": report.get("max_score"),
        "rule_results": _failed_rule_summaries(report),
    }
    return (
        "Analyze on-page SEO. Rules already ran; they own the score. "
        "Return JSON keys: semantic_coverage_score (0-100), notes (string array), "
        "title (1-3 strings in the title character band), "
        "meta_description (1-3 strings in the meta character band), "
        "alt_text (array of {src, suggested_alt}).\n"
        + json.dumps(payload, ensure_ascii=False)
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

    try:
        raw = client.chat_json(
            system=_system_prompt(thresholds),
            user=_user_prompt(
                page,
                report,
                keywords=keywords,
                thresholds=thresholds,
                readability=readability,
                missing_alts=missing_alts,
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
