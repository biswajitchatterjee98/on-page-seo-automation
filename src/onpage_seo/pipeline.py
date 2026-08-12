"""Multi-URL job runner: discover → crawl → duplicates → reports → store."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from onpage_seo.config import Settings
from onpage_seo.crawl import crawl_url
from onpage_seo.discover import resolve_targets
from onpage_seo.llm import build_client, enrich_report_with_llm
from onpage_seo.queue import enqueue_from_report
from onpage_seo.report import build_report
from onpage_seo.storage import JobRecord, Store, open_store

logger = logging.getLogger("onpage_seo")


@dataclass
class JobConfig:
    urls: list[str] = field(default_factory=list)
    url_file: str | None = None
    sitemap_url: str | None = None
    keywords: list[str] = field(default_factory=list)
    competitor_urls: list[str] = field(default_factory=list)
    competitor_word_count: float | None = None
    max_pages: int | None = None
    render_mode: str = "auto"
    ignore_robots: bool = False
    enforce_ssrf: bool = True
    fail_fast: bool = False
    reuse_completed: bool = True
    enable_llm: bool | None = None  # None → follow settings.llm_enabled

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def config_hash(self, rules_version: str) -> str:
        payload = {
            "config": self.to_dict(),
            "rules_version": rules_version,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _duplicate_values(values: list[str]) -> set[str]:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return {value for value, count in counts.items() if count > 1}


def _slack_summary(job_id: str, status: str, reports: list[dict[str, Any]]) -> str:
    lines = [f"On-page SEO job `{job_id}` → *{status}*", f"Pages: {len(reports)}"]
    failures = [r for r in reports if r.get("status") == "error"]
    if failures:
        lines.append(f"Crawl errors: {len(failures)}")
        for report in failures[:5]:
            lines.append(
                f"- {report.get('url')}: {report.get('error_code')} ({report.get('detail')})"
            )
    scored = [r for r in reports if r.get("status") == "ok"]
    for report in sorted(scored, key=lambda item: int(item.get("overall_score") or 0))[:8]:
        fails = [
            check["id"]
            for check in report.get("rules") or []
            if check.get("status") == "fail"
        ]
        fail_note = f" fails={','.join(fails[:4])}" if fails else ""
        lines.append(
            f"- {report.get('url')}: {report.get('overall_score')}/{report.get('max_score')}{fail_note}"
        )
    return "\n".join(lines)


def run_job(
    config: JobConfig,
    settings: Settings,
    *,
    store: Store | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    store = store or open_store(settings.database_url)
    store.ensure_schema()
    rules_version = settings.thresholds.rules_version
    config_hash = config.config_hash(rules_version)

    if config.reuse_completed:
        existing = store.find_completed_by_hash(config_hash)
        if existing and existing.summary_json:
            summary = dict(existing.summary_json)
            if job_id and job_id != existing.id:
                if store.get_job(job_id) is None:
                    store.create_job(
                        JobRecord(
                            id=job_id,
                            status=existing.status,
                            config_json=config.to_dict(),
                            config_hash=config_hash,
                            rules_version=rules_version,
                            summary_json=summary,
                        )
                    )
                else:
                    store.update_job(
                        job_id, status=existing.status, summary_json=summary
                    )
                summary = {**summary, "job_id": job_id, "reused_from": existing.id}
            logger.info(
                "reusing completed job",
                extra={
                    "job_id": summary.get("job_id", existing.id),
                    "url": "",
                    "stage": "idempotent",
                    "status": "ok",
                },
            )
            return summary

    job_id = job_id or str(uuid.uuid4())
    max_pages = config.max_pages or settings.max_pages
    targets = resolve_targets(
        urls=config.urls,
        url_file=Path(config.url_file) if config.url_file else None,
        sitemap_url=config.sitemap_url,
        settings=settings,
        max_pages=max_pages,
        enforce_ssrf=config.enforce_ssrf,
    )
    if not targets:
        raise ValueError("no target URLs resolved")

    existing_job = store.get_job(job_id)
    if existing_job is None:
        store.create_job(
            JobRecord(
                id=job_id,
                status="running",
                config_json=config.to_dict(),
                config_hash=config_hash,
                rules_version=rules_version,
            )
        )
    else:
        store.update_job(job_id, status="running")

    competitor_avg = config.competitor_word_count
    if competitor_avg is None and config.competitor_urls:
        word_counts: list[int] = []
        for competitor_url in config.competitor_urls[: max_pages]:
            page = crawl_url(
                competitor_url,
                settings,
                render_mode=config.render_mode,
                enforce_ssrf=config.enforce_ssrf,
                ignore_robots=config.ignore_robots,
            )
            if page.get("status") != "error":
                word_counts.append(int(page.get("word_count") or 0))
        if word_counts:
            competitor_avg = sum(word_counts) / len(word_counts)

    pages: list[dict[str, Any]] = []
    primary = config.keywords[0] if config.keywords else None
    for url in targets:
        page = crawl_url(
            url,
            settings,
            primary_keyword=primary,
            render_mode=config.render_mode,
            enforce_ssrf=config.enforce_ssrf,
            ignore_robots=config.ignore_robots,
        )
        pages.append(page)
        if config.fail_fast and page.get("status") == "error":
            break

    titles = [str(page.get("title") or "") for page in pages if page.get("status") != "error"]
    metas = [
        str(page.get("meta_description") or "")
        for page in pages
        if page.get("status") != "error"
    ]
    # Only enable duplicate checks when multi-page
    dup_titles = _duplicate_values(titles) if len(pages) > 1 else None
    dup_metas = _duplicate_values(metas) if len(pages) > 1 else None

    reports: list[dict[str, Any]] = []
    error_count = 0
    use_llm = settings.llm_enabled if config.enable_llm is None else config.enable_llm
    client = build_client(settings) if use_llm else None
    for page in pages:
        report = build_report(
            job_id=job_id,
            page=page,
            keywords=config.keywords,
            thresholds=settings.thresholds,
            competitor_avg_word_count=competitor_avg,
            duplicate_titles=dup_titles,
            duplicate_metas=dup_metas,
        )
        report = enrich_report_with_llm(
            report,
            page,
            keywords=config.keywords,
            thresholds=settings.thresholds,
            settings=settings,
            client=client,
            corpus=pages,
        )
        reports.append(report)
        is_error = page.get("status") == "error"
        if is_error:
            error_count += 1
        page_id = store.save_page(
            job_id,
            url=str(page.get("url") or report.get("url")),
            status="error" if is_error else "ok",
            http_status=page.get("http_status") if not is_error else None,
            error_code=page.get("error_code") if is_error else None,
            page_json=page,
        )
        report_id = store.save_report(page_id, int(report.get("overall_score") or 0), report)
        if not is_error:
            queued = enqueue_from_report(
                store,
                job_id=job_id,
                report_id=report_id,
                report=report,
                page=page,
            )
            report["queued_suggestion_ids"] = queued

    if error_count == 0:
        status = "completed"
    elif error_count == len(reports):
        status = "failed"
    else:
        status = "completed_with_errors"

    summary = {
        "job_id": job_id,
        "status": status,
        "rules_version": rules_version,
        "config_hash": config_hash,
        "page_count": len(reports),
        "error_count": error_count,
        "competitor_avg_word_count": competitor_avg,
        "reports": reports,
        "slack_text": _slack_summary(job_id, status, reports),
    }
    store.update_job(job_id, status=status, summary_json=summary)
    return summary
