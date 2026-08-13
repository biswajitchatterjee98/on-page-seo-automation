"""Approve / reject / apply suggestion queue with audit trail."""

from __future__ import annotations

import logging
import os
from typing import Any

from onpage_seo.cms.adapters import CmsAdapter, CmsError, load_cms_adapter
from onpage_seo.config import Settings
from onpage_seo.storage import Store
from onpage_seo.trends import detect_regressions

logger = logging.getLogger("onpage_seo")

TERMINAL_STATUSES = {"rejected", "applied"}


def approve_suggestion(store: Store, suggestion_id: int, *, actor: str) -> dict[str, Any]:
    suggestion = store.get_suggestion(suggestion_id)
    if not suggestion:
        raise ValueError(f"suggestion {suggestion_id} not found")
    if suggestion["status"] not in {"pending", "apply_failed"}:
        raise ValueError(f"cannot approve from status {suggestion['status']}")
    store.update_suggestion_status(suggestion_id, status="approved")
    store.add_audit_event(
        suggestion_id,
        action="approve",
        actor=actor,
        before_json={"status": suggestion["status"]},
        after_json={"status": "approved"},
        detail=None,
    )
    updated = store.get_suggestion(suggestion_id)
    assert updated is not None
    return updated


def reject_suggestion(store: Store, suggestion_id: int, *, actor: str, reason: str = "") -> dict[str, Any]:
    suggestion = store.get_suggestion(suggestion_id)
    if not suggestion:
        raise ValueError(f"suggestion {suggestion_id} not found")
    if suggestion["status"] in TERMINAL_STATUSES:
        raise ValueError(f"cannot reject from status {suggestion['status']}")
    store.update_suggestion_status(suggestion_id, status="rejected")
    store.add_audit_event(
        suggestion_id,
        action="reject",
        actor=actor,
        before_json={"status": suggestion["status"]},
        after_json={"status": "rejected"},
        detail=reason or None,
    )
    updated = store.get_suggestion(suggestion_id)
    assert updated is not None
    return updated


def _post_fix_verify(
    store: Store,
    suggestion: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """Re-crawl URL after live apply; alert if score regresses (DoD success metric)."""
    from onpage_seo.crawl import crawl_url
    from onpage_seo.report import build_report

    url = str(suggestion.get("url") or "")
    history = store.url_score_history(url, limit=20)
    baseline = int(history[-1]["overall_score"]) if history else None
    page = crawl_url(
        url,
        settings,
        render_mode="static",
        enforce_ssrf=settings.ssrf_guard,
        ignore_robots=False,
    )
    report = build_report(
        job_id=str(suggestion.get("job_id") or "post-fix"),
        page=page,
        keywords=[],
        thresholds=settings.thresholds,
    )
    new_score = int(report.get("overall_score") or 0)
    drop_points = int(os.environ.get("ONPAGE_SEO_REGRESSION_POINTS", "5"))
    result: dict[str, Any] = {
        "url": url,
        "baseline_score": baseline,
        "new_score": new_score,
        "crawl_status": page.get("status") or report.get("status"),
        "regressed": False,
        "alert_text": None,
    }
    if page.get("status") == "error":
        result["alert_text"] = (
            f"ALERT post-fix re-crawl failed for {url}: {page.get('error_code')} {page.get('detail')}"
        )
        return result
    if baseline is None:
        return result
    synthetic = [
        {"created_at": "1", "overall_score": baseline, "job_id": "baseline"},
        {"created_at": "2", "overall_score": new_score, "job_id": "post-fix"},
    ]
    regression = detect_regressions(synthetic, drop_points=drop_points)
    if regression and regression.get("regressed"):
        result["regressed"] = True
        result["alert_text"] = (
            f"ALERT post-fix regression on {url}: {baseline} → {new_score} "
            f"(Δ {regression['delta']}, threshold {drop_points})"
        )
    return result


def apply_suggestion(
    store: Store,
    suggestion_id: int,
    *,
    actor: str,
    settings: Settings,
    dry_run: bool = False,
    cms: CmsAdapter | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    suggestion = store.get_suggestion(suggestion_id)
    if not suggestion:
        raise ValueError(f"suggestion {suggestion_id} not found")
    if suggestion["status"] != "approved":
        # Hard gate: only approved items may touch CMS (or dry-run audit)
        raise ValueError(
            f"refusing to apply suggestion {suggestion_id}: status is {suggestion['status']!r}, need 'approved'"
        )

    before = suggestion.get("before_json") or {}
    payload = suggestion.get("payload_json") or {}
    adapter = cms or load_cms_adapter(
        provider=settings.cms_provider,
        wp_base_url=settings.wp_base_url,
        wp_username=settings.wp_username,
        wp_app_password=settings.wp_app_password,
    )

    if dry_run:
        after = {
            "url": suggestion["url"],
            "field": suggestion["field"],
            "value": payload.get("value"),
            "dry_run": True,
            "cms": adapter.name,
        }
        store.add_audit_event(
            suggestion_id,
            action="dry_run",
            actor=actor,
            before_json=before,
            after_json=after,
            detail="dry-run: CMS not mutated",
        )
        return {"suggestion": suggestion, "dry_run": True, "after": after}

    try:
        after = adapter.apply(
            url=str(suggestion["url"]),
            field=str(suggestion["field"]),
            payload=payload,
        )
        if not after.get("applied"):
            store.add_audit_event(
                suggestion_id,
                action="apply_skipped",
                actor=actor,
                before_json=before,
                after_json=after,
                detail="CMS adapter did not mutate (null/no-op)",
            )
            return {
                "suggestion": suggestion,
                "dry_run": False,
                "after": after,
                "skipped": True,
            }
        store.update_suggestion_status(suggestion_id, status="applied")
        store.add_audit_event(
            suggestion_id,
            action="apply",
            actor=actor,
            before_json=before,
            after_json=after,
            detail=None,
        )
        updated = store.get_suggestion(suggestion_id)
        result: dict[str, Any] = {"suggestion": updated, "dry_run": False, "after": after}
        if verify:
            verification = _post_fix_verify(store, suggestion, settings)
            result["verification"] = verification
            store.add_audit_event(
                suggestion_id,
                action="post_fix_verify",
                actor=actor,
                before_json={"baseline_score": verification.get("baseline_score")},
                after_json=verification,
                detail=verification.get("alert_text"),
            )
            if verification.get("alert_text"):
                result["alert_text"] = verification["alert_text"]
                logger.info(
                    "post-fix regression alert",
                    extra={
                        "job_id": suggestion.get("job_id"),
                        "url": suggestion.get("url"),
                        "stage": "post_fix_verify",
                        "status": "error",
                        "error_code": "post_fix_regression",
                    },
                )
        return result
    except (CmsError, Exception) as exc:  # noqa: BLE001 — boundary: mark apply_failed
        store.update_suggestion_status(suggestion_id, status="apply_failed")
        alert = (
            f"ALERT apply_failed suggestion={suggestion_id} url={suggestion.get('url')} "
            f"field={suggestion.get('field')}: {exc}"
        )
        store.add_audit_event(
            suggestion_id,
            action="apply_failed",
            actor=actor,
            before_json=before,
            after_json=None,
            detail=str(exc),
        )
        logger.info(
            "cms apply failed",
            extra={
                "job_id": suggestion.get("job_id"),
                "url": suggestion.get("url"),
                "stage": "cms_apply",
                "status": "error",
                "error_code": "apply_failed",
            },
        )
        updated = store.get_suggestion(suggestion_id)
        return {
            "suggestion": updated,
            "dry_run": False,
            "error": str(exc),
            "alert_text": alert,
        }
