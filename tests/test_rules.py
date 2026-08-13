"""Rule engine scoring checks — fail if weighted logic breaks."""

from __future__ import annotations

from pathlib import Path

from onpage_seo.config import load_thresholds
from onpage_seo.crawl.extract import extract_page, keyword_density_pct
from onpage_seo.rules.engine import evaluate_page, score_checks

FIXTURE = Path(__file__).parent / "fixtures" / "sample.html"


def _sample_page():
    html = FIXTURE.read_text(encoding="utf-8")
    return extract_page(
        html,
        url="https://example.com/espresso-machines",
        final_url="https://example.com/espresso-machines",
        http_status=200,
        render_mode="static",
        primary_keyword="espresso machines",
    )


def test_keyword_density_counts_phrase():
    text = "espresso machines are great. more espresso machines here."
    assert keyword_density_pct(text, "espresso machines") > 0


def test_evaluate_page_scores_fixture():
    thresholds = load_thresholds()
    page = _sample_page()
    checks = evaluate_page(page, ["espresso machines"], thresholds)
    by_id = {item["id"]: item for item in checks}

    assert by_id["keyword_in_title"]["status"] == "pass"
    assert by_id["keyword_in_h1"]["status"] == "pass"
    assert by_id["image_alt"]["status"] == "fail"  # one empty alt in fixture
    assert by_id["duplicate_title_meta"]["status"] == "skip"
    assert by_id["competitor_length"]["status"] == "skip"

    overall, maximum = score_checks(checks)
    assert maximum == sum(thresholds.weights.values())
    assert 0 <= overall <= maximum
    # empty alt must zero that weight
    assert by_id["image_alt"]["score"] == 0


def test_empty_title_fails():
    thresholds = load_thresholds()
    page = _sample_page()
    page["title"] = ""
    checks = evaluate_page(page, ["espresso machines"], thresholds)
    title_check = next(item for item in checks if item["id"] == "title_length")
    assert title_check["status"] == "fail"
    assert title_check["score"] == 0


def test_ssrf_blocks_localhost():
    from onpage_seo.security.ssrf import SsrfBlockedError, assert_url_safe
    import pytest

    with pytest.raises(SsrfBlockedError):
        assert_url_safe("http://127.0.0.1/")


def test_homepage_skips_keyword_in_url():
    thresholds = load_thresholds()
    page = _sample_page()
    page["url"] = "https://example.com/"
    page["final_url"] = "https://example.com/"
    checks = evaluate_page(page, ["espresso machines"], thresholds)
    by_id = {item["id"]: item for item in checks}
    assert by_id["keyword_in_url"]["status"] == "skip"
    assert by_id["schema_presence"]["status"] == "pass"
