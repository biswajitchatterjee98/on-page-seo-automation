"""Enqueue accepted LLM suggestions for human approval (never auto-publish)."""

from __future__ import annotations

from typing import Any

from onpage_seo.storage import Store

QUEUEABLE_FIELDS = ("title", "meta_description", "alt_text")


def build_queue_items(report: dict[str, Any], page: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn accepted llm.suggestions into pending queue rows (rejected stay out)."""
    if report.get("status") == "error":
        return []
    llm = report.get("llm") or {}
    suggestions = llm.get("suggestions") or {}
    url = str(report.get("url") or page.get("final_url") or page.get("url") or "")
    items: list[dict[str, Any]] = []

    for title in suggestions.get("title") or []:
        items.append(
            {
                "field": "title",
                "url": url,
                "payload": {"value": str(title)},
                "before": {"value": page.get("title") or ""},
            }
        )

    for meta in suggestions.get("meta_description") or []:
        items.append(
            {
                "field": "meta_description",
                "url": url,
                "payload": {"value": str(meta)},
                "before": {"value": page.get("meta_description") or ""},
            }
        )

    for alt in suggestions.get("alt_text") or []:
        if not isinstance(alt, dict):
            continue
        src = alt.get("src")
        current = alt.get("current_alt")
        if current is None and src:
            for img in page.get("images") or []:
                if img.get("src") == src:
                    current = img.get("alt") or ""
                    break
        items.append(
            {
                "field": "alt_text",
                "url": url,
                "payload": {
                    "value": str(alt.get("suggested_alt") or ""),
                    "src": src,
                },
                "before": {"value": current or "", "src": src},
            }
        )

    return items


def enqueue_from_report(
    store: Store,
    *,
    job_id: str,
    report_id: int,
    report: dict[str, Any],
    page: dict[str, Any],
) -> list[int]:
    ids: list[int] = []
    for item in build_queue_items(report, page):
        suggestion_id = store.create_suggestion(
            report_id=report_id,
            job_id=job_id,
            url=item["url"],
            field=item["field"],
            payload_json=item["payload"],
            before_json=item["before"],
            status="pending",
        )
        ids.append(suggestion_id)
    return ids
