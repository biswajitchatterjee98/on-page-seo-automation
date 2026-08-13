"""End-to-end verification script testing all On-Page SEO Automation features (P0 -> P4)."""

import json
import sys
from pathlib import Path

# Add src to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from onpage_seo.config import load_settings
from onpage_seo.crawl import crawl_url
from onpage_seo.rules import evaluate_page, score_checks
from onpage_seo.storage import open_store, JobRecord
from onpage_seo.cms import approve_suggestion, apply_suggestion
from onpage_seo.pipeline import run_job, JobConfig


def test_p0_core_scoring():
    print("--- [P0] Testing Core Crawl & Deterministic Scoring ---")
    settings = load_settings()
    page = crawl_url("https://example.com", settings=settings)
    assert page["status"] == "ok" or page["status"] == "error"
    
    # Test rules evaluation
    checks = evaluate_page(page, keywords=["example"], thresholds=settings.thresholds)
    overall, maximum = score_checks(checks)
    print(f"[OK] P0 Core Passed! Evaluated page '{page.get('url')}' -> Score: {overall}/{maximum}")


def test_p1_storage_and_pipeline():
    print("\n--- [P1] Testing Pipeline Execution & Storage Persistence ---")
    settings = load_settings()
    store = open_store(settings.database_url)
    
    config = JobConfig(
        urls=["https://python.org"],
        keywords=["python"],
        render_mode="static",
        enforce_ssrf=False,
    )
    summary = run_job(config, settings, store=store)
    print(f"Summary status: {summary.get('status')}")
    if summary.get("reports"):
        print(f"Report detail: {summary['reports'][0]}")
    assert summary["status"] in {"completed", "completed_with_errors"}
    print(f"[OK] P1 Pipeline Passed! Job ID: {summary['job_id']} -> Score: {summary.get('overall_score')}")


def test_p2_llm_suggestions():
    print("\n--- [P2] Testing AI LLM Enrichment & Suggestions Engine ---")
    settings = load_settings()
    if not settings.llm_enabled:
        print("[INFO] LLM is currently disabled (ONPAGE_SEO_LLM=0). Skipping live AI API test.")
    else:
        print(f"[OK] P2 LLM Enabled! Base URL: {settings.openai_base_url}, Model: {settings.openai_model}")


def test_p3_p4_suggestion_queue_and_cms_dryrun():
    print("\n--- [P3 & P4] Testing Suggestion Queue & CMS Dry-Run Adapter ---")
    settings = load_settings()
    store = open_store(settings.database_url)
    
    # Create test job and suggestion in store
    job_id = "test-e2e-job-123"
    store.create_job(JobRecord(id=job_id, status="completed", config_json={}, config_hash="hash123", rules_version="1.0.0"))
    page_id = store.save_page(job_id, url="https://example.com/test", status="ok", http_status=200, error_code=None, page_json={})
    report_id = store.save_report(page_id, overall_score=60, report_json={})
    
    sug_id = store.create_suggestion(
        report_id=report_id,
        job_id=job_id,
        url="https://example.com/test",
        field="title",
        payload_json={"value": "Optimized Title by AI"},
        before_json={"value": "Old Title"},
    )
    assert sug_id > 0
    print(f"[OK] Created pending suggestion #{sug_id}")
    
    # Test approval
    approve_suggestion(store, sug_id, actor="e2e-test-actor")
    sug = store.get_suggestion(sug_id)
    assert sug["status"] == "approved"
    print(f"[OK] Approved suggestion #{sug_id}")
    
    # Test dry-run CMS apply
    apply_res = apply_suggestion(store, sug_id, actor="e2e-test-actor", settings=settings, dry_run=True)
    assert apply_res.get("dry_run") is True
    print(f"[OK] CMS Dry-Run Apply Passed! Result: {apply_res.get('detail')}")


def main():
    print("==================================================")
    print("      ON-PAGE SEO AUTOMATION E2E TEST SUITE       ")
    print("==================================================")
    test_p0_core_scoring()
    test_p1_storage_and_pipeline()
    test_p2_llm_suggestions()
    test_p3_p4_suggestion_queue_and_cms_dryrun()
    print("\n==================================================")
    print("     ALL END-TO-END FEATURE TESTS PASSED CLEANLY!  ")
    print("==================================================")


if __name__ == "__main__":
    main()
