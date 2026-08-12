"""P1 discover + duplicate + API auth/SSRF checks."""

from __future__ import annotations

from fastapi.testclient import TestClient

from onpage_seo.config import load_settings
from onpage_seo.discover import dedupe_cap, parse_sitemap_xml
from onpage_seo.pipeline import JobConfig, _duplicate_values, run_job
from onpage_seo.storage import MemoryStore


def test_parse_sitemap_urlset():
    xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/a</loc></url>
      <url><loc>https://example.com/b</loc></url>
    </urlset>
    """
    pages, children = parse_sitemap_xml(xml)
    assert children == []
    assert pages == ["https://example.com/a", "https://example.com/b"]


def test_parse_sitemap_index():
    xml = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
    </sitemapindex>
    """
    pages, children = parse_sitemap_xml(xml)
    assert pages == []
    assert children == ["https://example.com/sitemap-1.xml"]


def test_dedupe_cap():
    assert dedupe_cap(
        ["https://a.com/1", "https://a.com/1", "ftp://x", "https://a.com/2"],
        max_pages=10,
    ) == ["https://a.com/1", "https://a.com/2"]


def test_duplicate_values():
    assert _duplicate_values(["A", "B", "A", ""]) == {"A"}


def test_batch_marks_duplicate_titles(monkeypatch):
    settings = load_settings()
    store = MemoryStore()

    pages = {
        "https://example.com/1": {
            "url": "https://example.com/1",
            "final_url": "https://example.com/1",
            "http_status": 200,
            "title": "Same Title",
            "meta_description": "meta one " + ("x" * 140),
            "canonical": "https://example.com/1",
            "headers": {"h1": ["Same Title"], "h2": [], "h3": [], "h4": [], "h5": [], "h6": []},
            "body_text": "keyword " + ("word " * 400),
            "word_count": 401,
            "keyword_density": {"primary": 1.0},
            "images": [],
            "links": {"internal": [], "external": []},
            "schema": [],
            "render_mode": "static",
            "robots_allowed": True,
            "fetched_at": "2026-01-01T00:00:00Z",
        },
        "https://example.com/2": {
            "url": "https://example.com/2",
            "final_url": "https://example.com/2",
            "http_status": 200,
            "title": "Same Title",
            "meta_description": "meta two " + ("y" * 140),
            "canonical": "https://example.com/2",
            "headers": {"h1": ["Other"], "h2": [], "h3": [], "h4": [], "h5": [], "h6": []},
            "body_text": "keyword " + ("word " * 400),
            "word_count": 401,
            "keyword_density": {"primary": 1.0},
            "images": [],
            "links": {"internal": [], "external": []},
            "schema": [],
            "render_mode": "static",
            "robots_allowed": True,
            "fetched_at": "2026-01-01T00:00:00Z",
        },
    }

    def fake_crawl(url, *_args, **_kwargs):
        return pages[url]

    monkeypatch.setattr("onpage_seo.pipeline.crawl_url", fake_crawl)
    monkeypatch.setattr(
        "onpage_seo.pipeline.resolve_targets",
        lambda **_kwargs: list(pages.keys()),
    )

    summary = run_job(
        JobConfig(
            urls=list(pages.keys()),
            keywords=["keyword"],
            reuse_completed=False,
            enforce_ssrf=False,
        ),
        settings,
        store=store,
        job_id="job-dup",
    )
    assert summary["status"] == "completed"
    for report in summary["reports"]:
        dup = next(item for item in report["rules"] if item["id"] == "duplicate_title_meta")
        assert dup["status"] == "fail"


def test_api_rejects_ssrf_and_missing_auth(monkeypatch):
    monkeypatch.setenv("ONPAGE_SEO_API_TOKEN", "secret")
    import onpage_seo.api.server as api_mod
    from onpage_seo.storage import MemoryStore

    memory = MemoryStore()
    monkeypatch.setattr(api_mod, "_store", memory)

    client = TestClient(api_mod.app)
    assert client.get("/health").status_code == 200
    assert client.post("/jobs", json={"urls": ["https://example.com"]}).status_code == 401

    blocked = client.post(
        "/jobs",
        headers={"Authorization": "Bearer secret"},
        json={"urls": ["http://127.0.0.1/"], "sync": True},
    )
    assert blocked.status_code == 400
    assert "ssrf_blocked" in blocked.json()["detail"]
