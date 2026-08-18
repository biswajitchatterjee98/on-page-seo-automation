"""P4 suggestion queue, approval gate, dry-run apply, audit."""

from __future__ import annotations

from onpage_seo.cms import NullCmsAdapter, apply_suggestion, approve_suggestion, reject_suggestion
from onpage_seo.cms.adapters import CmsError
from onpage_seo.config import load_settings
from onpage_seo.queue import build_queue_items, enqueue_from_report
from onpage_seo.storage import JobRecord, MemoryStore


def _seed_report_page():
    page = {
        "url": "https://example.com/post",
        "final_url": "https://example.com/post",
        "title": "Old title that is plenty long enough!!",
        "meta_description": "Old meta " + ("m" * 140),
        "images": [{"src": "/a.jpg", "alt": ""}],
    }
    report = {
        "status": "ok",
        "url": page["url"],
        "llm": {
            "status": "ok",
            "suggestions": {
                "title": ["Best espresso machines for home baristas now"],
                "meta_description": [],
                "alt_text": [{"src": "/a.jpg", "suggested_alt": "Steel espresso machine"}],
                "internal_links": [],
            },
            "rejected_suggestions": [
                {"field": "title", "value": "x", "reason": "too short"},
            ],
        },
    }
    return page, report


def test_rejected_suggestions_never_queued():
    page, report = _seed_report_page()
    items = build_queue_items(report, page)
    fields = {item["field"] for item in items}
    assert "title" in fields
    assert "alt_text" in fields
    # rejected short title must not appear
    assert all(item["payload"]["value"] != "x" for item in items)


def test_only_approved_can_apply():
    store = MemoryStore()
    settings = load_settings()
    store.create_job(
        JobRecord(
            id="job-1",
            status="completed",
            config_json={},
            config_hash="h",
            rules_version="1.0.0",
        )
    )
    page_id = store.save_page(
        "job-1",
        url="https://example.com/post",
        status="ok",
        http_status=200,
        error_code=None,
        page_json={},
    )
    page, report = _seed_report_page()
    report_id = store.save_report(page_id, 80, report)
    ids = enqueue_from_report(
        store, job_id="job-1", report_id=report_id, report=report, page=page
    )
    assert ids
    sid = ids[0]

    try:
        apply_suggestion(
            store, sid, actor="test", settings=settings, dry_run=True, cms=NullCmsAdapter()
        )
        assert False, "expected ValueError for pending apply"
    except ValueError as exc:
        assert "approved" in str(exc)

    approve_suggestion(store, sid, actor="reviewer")
    result = apply_suggestion(
        store,
        sid,
        actor="reviewer",
        settings=settings,
        dry_run=True,
        cms=NullCmsAdapter(),
    )
    assert result["dry_run"] is True
    assert store.get_suggestion(sid)["status"] == "approved"  # dry-run keeps approved
    events = store.list_audit_events(sid)
    assert any(event["action"] == "approve" for event in events)
    assert any(event["action"] == "dry_run" for event in events)
    dry_event = next(event for event in events if event["action"] == "dry_run")
    assert dry_event["before_json"] is not None
    assert dry_event["after_json"]["dry_run"] is True

    # live apply with null CMS must not mark applied
    result_live = apply_suggestion(
        store,
        sid,
        actor="reviewer",
        settings=settings,
        dry_run=False,
        cms=NullCmsAdapter(),
        verify=False,
    )
    assert result_live.get("skipped") is True
    assert store.get_suggestion(sid)["status"] == "approved"


def test_live_apply_with_null_cms_and_reject_gate():
    store = MemoryStore()
    settings = load_settings()
    store.create_job(
        JobRecord(
            id="job-2",
            status="completed",
            config_json={},
            config_hash="h2",
            rules_version="1.0.0",
        )
    )
    page_id = store.save_page(
        "job-2",
        url="https://example.com/post",
        status="ok",
        http_status=200,
        error_code=None,
        page_json={},
    )
    page, report = _seed_report_page()
    report_id = store.save_report(page_id, 80, report)
    sid = enqueue_from_report(
        store, job_id="job-2", report_id=report_id, report=report, page=page
    )[0]

    reject_suggestion(store, sid, actor="reviewer", reason="bad copy")
    assert store.get_suggestion(sid)["status"] == "rejected"
    try:
        apply_suggestion(
            store, sid, actor="reviewer", settings=settings, dry_run=False, cms=NullCmsAdapter()
        )
        assert False, "rejected must not apply"
    except ValueError:
        pass


def test_apply_failed_status(monkeypatch):
    store = MemoryStore()
    settings = load_settings()
    store.create_job(
        JobRecord(
            id="job-3",
            status="completed",
            config_json={},
            config_hash="h3",
            rules_version="1.0.0",
        )
    )
    page_id = store.save_page(
        "job-3",
        url="https://example.com/post",
        status="ok",
        http_status=200,
        error_code=None,
        page_json={},
    )
    page, report = _seed_report_page()
    report_id = store.save_report(page_id, 80, report)
    sid = enqueue_from_report(
        store, job_id="job-3", report_id=report_id, report=report, page=page
    )[0]
    approve_suggestion(store, sid, actor="reviewer")

    class BoomCms:
        name = "boom"

        def apply(self, **_kwargs):
            raise CmsError("boom")

    result = apply_suggestion(
        store,
        sid,
        actor="reviewer",
        settings=settings,
        dry_run=False,
        cms=BoomCms(),
        verify=False,
    )
    assert result.get("error")
    assert result.get("alert_text")
    assert store.get_suggestion(sid)["status"] == "apply_failed"
    assert any(event["action"] == "apply_failed" for event in store.list_audit_events(sid))


def test_post_fix_verify_flags_regression(monkeypatch):
    from onpage_seo.cms import fix as fix_mod

    store = MemoryStore()
    settings = load_settings()
    store.create_job(
        JobRecord(
            id="job-4",
            status="completed",
            config_json={},
            config_hash="h4",
            rules_version="1.0.0",
        )
    )
    url = "https://example.com/post"
    page_id = store.save_page(
        "job-4", url=url, status="ok", http_status=200, error_code=None, page_json={}
    )
    store.save_report(page_id, 90, {"rules": []})
    page, report = _seed_report_page()
    report_id = store.save_report(page_id, 90, report)
    sid = enqueue_from_report(
        store, job_id="job-4", report_id=report_id, report=report, page=page
    )[0]
    approve_suggestion(store, sid, actor="reviewer")

    monkeypatch.setattr(
        fix_mod,
        "_post_fix_verify",
        lambda *_a, **_k: {
            "url": url,
            "baseline_score": 90,
            "new_score": 70,
            "regressed": True,
            "alert_text": "ALERT post-fix regression",
        },
    )
    class OkCms:
        name = "ok"

        def apply(self, **kwargs):
            return {"applied": True, "url": kwargs.get("url"), "field": kwargs.get("field")}

    result = apply_suggestion(
        store,
        sid,
        actor="reviewer",
        settings=settings,
        dry_run=False,
        cms=OkCms(),
        verify=True,
    )
    assert result["suggestion"]["status"] == "applied"
    assert result.get("alert_text")
    assert any(e["action"] == "post_fix_verify" for e in store.list_audit_events(sid))
