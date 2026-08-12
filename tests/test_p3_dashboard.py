"""P3 history, regressions, dashboard auth, keyword adapter."""

from __future__ import annotations

import json
from pathlib import Path

from onpage_seo.dashboard_auth import dashboard_credentials_configured, verify_dashboard_login
from onpage_seo.keywords import JsonKeywordSource, ManualKeywordSource, load_keyword_source
from onpage_seo.storage import JobRecord, MemoryStore
from onpage_seo.trends import detect_regressions, failed_check_ids


def test_failed_check_ids():
    assert failed_check_ids({"rules": [{"id": "a", "status": "fail"}, {"id": "b", "status": "pass"}]}) == [
        "a"
    ]


def test_detect_regression():
    history = [
        {"created_at": "2026-01-01", "overall_score": 80, "job_id": "1"},
        {"created_at": "2026-01-02", "overall_score": 70, "job_id": "2"},
    ]
    result = detect_regressions(history, drop_points=5)
    assert result is not None
    assert result["regressed"] is True
    assert result["delta"] == -10


def test_no_regression_on_improve():
    history = [
        {"created_at": "2026-01-01", "overall_score": 70, "job_id": "1"},
        {"created_at": "2026-01-02", "overall_score": 85, "job_id": "2"},
    ]
    result = detect_regressions(history, drop_points=5)
    assert result is not None
    assert result["regressed"] is False


def test_memory_store_url_history_two_runs():
    store = MemoryStore()
    url = "https://example.com/page"
    for index, score in enumerate((90, 75), start=1):
        job_id = f"job-{index}"
        store.create_job(
            JobRecord(
                id=job_id,
                status="completed",
                config_json={},
                config_hash=f"h{index}",
                rules_version="1.0.0",
            )
        )
        page_id = store.save_page(
            job_id,
            url=url,
            status="ok",
            http_status=200,
            error_code=None,
            page_json={"url": url},
        )
        store.save_report(
            page_id,
            score,
            {"rules": [{"id": "thin_content", "status": "fail" if score < 80 else "pass"}]},
        )

    history = store.url_score_history(url)
    assert len(history) == 2
    assert [row["overall_score"] for row in history] == [90, 75]
    regression = detect_regressions(history, drop_points=5)
    assert regression and regression["regressed"] is True


def test_dashboard_auth(monkeypatch):
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    assert dashboard_credentials_configured() is False
    assert verify_dashboard_login("a", "b") is False

    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    assert dashboard_credentials_configured() is True
    assert verify_dashboard_login("admin", "secret") is True
    assert verify_dashboard_login("admin", "wrong") is False


def test_json_keyword_source(tmp_path: Path):
    path = tmp_path / "keywords.json"
    path.write_text(
        json.dumps({"https://example.com": ["espresso", "coffee"]}),
        encoding="utf-8",
    )
    source = load_keyword_source(json_path=str(path))
    assert isinstance(source, JsonKeywordSource)
    assert source.keywords_for_url("https://example.com") == ["espresso", "coffee"]
    assert ManualKeywordSource(["x"]).keywords_for_url("any") == ["x"]
