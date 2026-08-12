"""FastAPI job API — auth token + SSRF on inbound URLs."""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from onpage_seo.cms import apply_suggestion, approve_suggestion, reject_suggestion
from onpage_seo.config import load_settings
from onpage_seo.pipeline import JobConfig, run_job
from onpage_seo.security.ssrf import SsrfBlockedError, assert_url_safe
from onpage_seo.storage import JobRecord, Store, open_store

app = FastAPI(title="On-Page SEO API", version="0.5.0")
_run_lock = threading.Lock()
_store: Store | None = None
_store_lock = threading.Lock()


class CreateJobBody(BaseModel):
    urls: list[HttpUrl] = Field(default_factory=list)
    sitemap_url: HttpUrl | None = None
    keywords: list[str] = Field(default_factory=list)
    competitor_urls: list[HttpUrl] = Field(default_factory=list)
    competitor_word_count: float | None = None
    max_pages: int | None = None
    render_mode: str = "auto"
    ignore_robots: bool = False
    fail_fast: bool = False
    reuse_completed: bool = True
    sync: bool = False
    enable_llm: bool | None = None


class ActorBody(BaseModel):
    actor: str = "api"
    reason: str = ""


class ApplyBody(BaseModel):
    actor: str = "api"
    dry_run: bool | None = None


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            settings = load_settings()
            _store = open_store(settings.database_url)
            _store.ensure_schema()
        return _store


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ONPAGE_SEO_API_TOKEN") or ""
    if not expected:
        raise HTTPException(status_code=500, detail="ONPAGE_SEO_API_TOKEN is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="invalid token")


def _validate_urls(urls: list[str]) -> None:
    for url in urls:
        try:
            assert_url_safe(url)
        except SsrfBlockedError as exc:
            raise HTTPException(status_code=400, detail=f"ssrf_blocked: {exc}") from exc


def _to_config(body: CreateJobBody) -> JobConfig:
    urls = [str(url) for url in body.urls]
    competitors = [str(url) for url in body.competitor_urls]
    sitemap = str(body.sitemap_url) if body.sitemap_url else None
    _validate_urls(urls + competitors + ([sitemap] if sitemap else []))
    if not urls and not sitemap:
        raise HTTPException(status_code=400, detail="urls or sitemap_url required")
    return JobConfig(
        urls=urls,
        sitemap_url=sitemap,
        keywords=body.keywords,
        competitor_urls=competitors,
        competitor_word_count=body.competitor_word_count,
        max_pages=body.max_pages,
        render_mode=body.render_mode,
        ignore_robots=body.ignore_robots,
        enforce_ssrf=True,
        fail_fast=body.fail_fast,
        reuse_completed=body.reuse_completed,
        enable_llm=body.enable_llm,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs", dependencies=[Depends(require_token)])
def create_job(body: CreateJobBody, background: BackgroundTasks) -> dict[str, Any]:
    settings = load_settings()
    config = _to_config(body)
    store = get_store()

    if body.sync:
        return run_job(config, settings, store=store)

    job_id = str(uuid.uuid4())
    rules_version = settings.thresholds.rules_version
    store.create_job(
        JobRecord(
            id=job_id,
            status="queued",
            config_json=config.to_dict(),
            config_hash=config.config_hash(rules_version),
            rules_version=rules_version,
        )
    )

    def _run() -> None:
        with _run_lock:
            run_job(config, settings, store=store, job_id=job_id)

    background.add_task(_run)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> dict[str, Any]:
    job = get_store().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job.id,
        "status": job.status,
        "rules_version": job.rules_version,
        "config_hash": job.config_hash,
        "summary": job.summary_json,
    }


@app.get("/jobs/{job_id}/summary", dependencies=[Depends(require_token)])
def get_job_summary(job_id: str) -> dict[str, str]:
    job = get_store().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if not job.summary_json:
        return {"job_id": job_id, "status": job.status, "text": f"Job {job_id} is {job.status}"}
    return {
        "job_id": job_id,
        "status": job.status,
        "text": str(job.summary_json.get("slack_text") or ""),
    }


@app.get("/suggestions", dependencies=[Depends(require_token)])
def list_suggestions(status: str | None = "pending", job_id: str | None = None) -> dict[str, Any]:
    rows = get_store().list_suggestions(status=status, job_id=job_id, limit=200)
    return {"count": len(rows), "suggestions": rows}


@app.get("/suggestions/{suggestion_id}", dependencies=[Depends(require_token)])
def get_suggestion(suggestion_id: int) -> dict[str, Any]:
    item = get_store().get_suggestion(suggestion_id)
    if not item:
        raise HTTPException(status_code=404, detail="suggestion not found")
    events = get_store().list_audit_events(suggestion_id)
    return {"suggestion": item, "audit": events}


@app.post("/suggestions/{suggestion_id}/approve", dependencies=[Depends(require_token)])
def api_approve(suggestion_id: int, body: ActorBody) -> dict[str, Any]:
    try:
        return approve_suggestion(get_store(), suggestion_id, actor=body.actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/suggestions/{suggestion_id}/reject", dependencies=[Depends(require_token)])
def api_reject(suggestion_id: int, body: ActorBody) -> dict[str, Any]:
    try:
        return reject_suggestion(get_store(), suggestion_id, actor=body.actor, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/suggestions/{suggestion_id}/apply", dependencies=[Depends(require_token)])
def api_apply(suggestion_id: int, body: ApplyBody) -> dict[str, Any]:
    settings = load_settings()
    dry_run = settings.cms_dry_run_default if body.dry_run is None else body.dry_run
    try:
        return apply_suggestion(
            get_store(),
            suggestion_id,
            actor=body.actor,
            settings=settings,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
