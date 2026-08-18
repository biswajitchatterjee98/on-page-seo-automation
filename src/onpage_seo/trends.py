"""URL score history + regression helpers (dashboard reads these)."""

from __future__ import annotations

from typing import Any


def detect_regressions(
    history: list[dict[str, Any]],
    *,
    drop_points: int = 5,
) -> dict[str, Any] | None:
    """history oldest→newest or newest-first; uses two most recent scores."""
    scored = [row for row in history if row.get("overall_score") is not None]
    if len(scored) < 2:
        return None
    # assume created_at ascending; take last two
    ordered = sorted(scored, key=lambda row: str(row.get("created_at") or ""))
    previous, latest = ordered[-2], ordered[-1]
    prev_score = int(previous["overall_score"])
    latest_score = int(latest["overall_score"])
    delta = latest_score - prev_score
    if delta <= -drop_points:
        return {
            "regressed": True,
            "previous_score": prev_score,
            "latest_score": latest_score,
            "delta": delta,
            "previous_job_id": previous.get("job_id"),
            "latest_job_id": latest.get("job_id"),
        }
    return {
        "regressed": False,
        "previous_score": prev_score,
        "latest_score": latest_score,
        "delta": delta,
        "previous_job_id": previous.get("job_id"),
        "latest_job_id": latest.get("job_id"),
    }


def failed_check_ids(report_json: dict[str, Any] | None) -> list[str]:
    if not report_json:
        return []
    return [
        str(check.get("id"))
        for check in report_json.get("rules") or []
        if check.get("status") == "fail"
    ]
