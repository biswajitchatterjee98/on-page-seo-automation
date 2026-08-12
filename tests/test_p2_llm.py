"""P0 robots check + P2 LLM validator / degrade / embeddings."""

from __future__ import annotations

from onpage_seo.config import load_settings
from onpage_seo.embeddings import suggest_internal_links
from onpage_seo.llm.analyze import enrich_report_with_llm
from onpage_seo.llm.client import LlmBudget, LlmClient
from onpage_seo.readability import flesch_reading_ease
from onpage_seo.report import build_report
from onpage_seo.validate import partition_suggestions, validate_title


def test_robots_disallowed(monkeypatch):
    from onpage_seo.crawl import fetch as fetch_mod

    settings = load_settings()
    monkeypatch.setattr(fetch_mod, "_robots_allowed", lambda *_a, **_k: False)
    page = fetch_mod.crawl_url(
        "https://example.com/secret",
        settings,
        enforce_ssrf=False,
    )
    assert page["status"] == "error"
    assert page["error_code"] == "robots_disallowed"
    report = build_report(
        job_id="r1",
        page=page,
        keywords=["x"],
        thresholds=settings.thresholds,
    )
    assert report["status"] == "error"
    assert report["overall_score"] == 0
    assert report["rules"] == []


def test_invalid_title_suggestion_rejected():
    thresholds = load_settings().thresholds
    ok, _ = validate_title("short", thresholds)
    assert ok is False
    accepted, rejected = partition_suggestions(
        {
            "title": ["short", "Best espresso machines for home baristas today!!"],
            "meta_description": [],
            "alt_text": [{"src": "/a.jpg", "suggested_alt": ""}],
            "internal_links": [],
        },
        thresholds,
    )
    assert rejected
    assert all(item["field"] != "title" or "length" in item["reason"] or "empty" in item["reason"] for item in rejected if item["field"] == "title")
    # empty alt rejected — must never be "queued"
    assert any(item["field"] == "alt_text" for item in rejected)
    # only length-valid titles accepted
    for title in accepted["title"]:
        assert thresholds.title_min <= len(title) <= thresholds.title_max


def test_llm_outage_keeps_rules_report(monkeypatch):
    settings = load_settings()
    page = {
        "url": "https://example.com/p",
        "final_url": "https://example.com/p",
        "http_status": 200,
        "title": "Best espresso machines for home baristas guide",
        "meta_description": "Compare the best espresso machines for home baristas, with brew tips, maintenance advice, and buying criteria so you pick the right machine.",
        "canonical": "https://example.com/p",
        "headers": {"h1": ["Best espresso machines"], "h2": [], "h3": [], "h4": [], "h5": [], "h6": []},
        "body_text": "espresso machines " + ("word " * 400),
        "word_count": 401,
        "keyword_density": {"primary": 1.0},
        "images": [],
        "links": {"internal": [], "external": []},
        "schema": [],
        "render_mode": "static",
        "robots_allowed": True,
    }
    report = build_report(
        job_id="j1",
        page=page,
        keywords=["espresso machines"],
        thresholds=settings.thresholds,
    )
    assert report["status"] == "ok"
    assert report["overall_score"] > 0

    client = LlmClient(
        api_key="x",
        base_url="https://example.invalid/v1",
        model="test",
        max_tokens=100,
        budget=LlmBudget(max_calls=2),
    )

    def boom(*_a, **_k):
        raise RuntimeError("simulated outage")

    monkeypatch.setattr(client, "chat_json", boom)
    enriched = enrich_report_with_llm(
        report,
        page,
        keywords=["espresso machines"],
        thresholds=settings.thresholds,
        settings=settings,
        client=client,
        corpus=[page],
    )
    assert enriched["overall_score"] == report["overall_score"]
    assert enriched["llm"]["status"] == "error"
    assert enriched["rules"]


def test_internal_link_suggestions():
    corpus = []
    for index in range(3):
        corpus.append(
            {
                "url": f"https://example.com/{index}",
                "final_url": f"https://example.com/{index}",
                "title": f"espresso machines guide {index}",
                "body_text": "espresso machines portafilter steam wand " * 20,
                "status": "ok",
            }
        )
    suggestions = suggest_internal_links(corpus[0], corpus, limit=2, min_score=0.01)
    assert suggestions
    assert suggestions[0]["to"] != corpus[0]["url"]


def test_flesch_score_runs():
    assert isinstance(flesch_reading_ease("This is a simple sentence. Another one follows!"), float)
