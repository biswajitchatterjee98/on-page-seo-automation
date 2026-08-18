"""CLI: onpage-seo audit — P0 end-to-end crawl + rules report."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from onpage_seo.config import load_settings
from onpage_seo.crawl import crawl_url
from onpage_seo.logutil import configure_logging, stage_timer
from onpage_seo.report import build_report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="onpage-seo",
        description="On-page SEO automation (P0: crawl + rule score → JSON report)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="Crawl one URL and score with the rule engine")
    audit.add_argument("--url", required=True, help="Page URL to audit")
    audit.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Target keyword (repeatable). First keyword is primary.",
    )
    audit.add_argument(
        "--render-mode",
        choices=("auto", "static", "playwright"),
        default="auto",
        help="HTML fetch strategy (default: auto)",
    )
    audit.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Do not honor robots.txt (audited use only)",
    )
    audit.add_argument(
        "--no-ssrf-guard",
        action="store_true",
        help="Disable SSRF IP checks (local fixtures / trusted URLs only)",
    )
    audit.add_argument(
        "--competitor-word-count",
        type=float,
        default=None,
        help="Optional competitor average word count for length compare",
    )
    audit.add_argument(
        "--job-id",
        default=None,
        help="Optional job id (default: generated UUID)",
    )
    audit.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write report JSON to this path (also prints to stdout)",
    )
    audit.add_argument(
        "--page-only",
        action="store_true",
        help="Emit crawler page JSON only (skip rules)",
    )
    return parser.parse_args(argv)


def run_audit(args: argparse.Namespace) -> int:
    logger = configure_logging()
    settings = load_settings()
    job_id = args.job_id or str(uuid.uuid4())
    keywords = list(args.keyword or [])
    url = args.url

    logger.info(
        "audit started",
        extra={"job_id": job_id, "url": url, "stage": "start", "status": "ok"},
    )

    with stage_timer(logger, job_id=job_id, url=url, stage="crawl"):
        page = crawl_url(
            url,
            settings,
            primary_keyword=keywords[0] if keywords else None,
            render_mode=args.render_mode,
            enforce_ssrf=not args.no_ssrf_guard,
            ignore_robots=args.ignore_robots,
        )

    if args.page_only:
        payload = page
    else:
        with stage_timer(logger, job_id=job_id, url=url, stage="rules"):
            payload = build_report(
                job_id=job_id,
                page=page,
                keywords=keywords,
                thresholds=settings.thresholds,
                competitor_avg_word_count=args.competitor_word_count,
            )

    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")

    if page.get("status") == "error":
        logger.info(
            "audit finished with crawl error",
            extra={
                "job_id": job_id,
                "url": url,
                "stage": "done",
                "status": "error",
                "error_code": page.get("error_code"),
            },
        )
        return 2

    logger.info(
        "audit finished",
        extra={"job_id": job_id, "url": url, "stage": "done", "status": "ok"},
    )
    return 0


def run_batch(args: argparse.Namespace) -> int:
    logger = configure_logging()
    settings = load_settings()
    if not args.url and not args.url_file and not args.sitemap_url:
        raise SystemExit("batch requires --url, --url-file, and/or --sitemap-url")

    config = JobConfig(
        urls=list(args.url or []),
        url_file=str(args.url_file) if args.url_file else None,
        sitemap_url=args.sitemap_url,
        keywords=list(args.keyword or []),
        competitor_urls=list(args.competitor_url or []),
        competitor_word_count=args.competitor_word_count,
        max_pages=args.max_pages,
        render_mode=args.render_mode,
        ignore_robots=args.ignore_robots,
        enforce_ssrf=not args.no_ssrf_guard,
        fail_fast=args.fail_fast,
        reuse_completed=not args.no_reuse,
        enable_llm=_llm_flag(args),
    )
    store = open_store(settings.database_url)
    summary = run_job(config, settings, store=store, job_id=args.job_id)
    _write_out(summary, args.out)
    if args.html_out:
        args.html_out.parent.mkdir(parents=True, exist_ok=True)
        args.html_out.write_text(render_batch_html(summary), encoding="utf-8")
    status = summary.get("status")
    logger.info(
        "batch finished",
        extra={
            "job_id": summary.get("job_id"),
            "url": "",
            "stage": "done",
            "status": "ok" if status != "failed" else "error",
        },
    )
    if status == "failed":
        return 2
    if status == "completed_with_errors":
        return 1
    return 0


def run_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from onpage_seo.storage import is_postgres_url

    settings = load_settings()
    if not is_postgres_url(settings.database_url):
        raise SystemExit(
            "onpage-seo serve requires DATABASE_URL=postgresql://... "
            "(in-memory store cannot survive restarts)"
        )
    uvicorn.run("onpage_seo.api.server:app", host=args.host, port=args.port, reload=False)
    return 0


def run_dashboard(args: argparse.Namespace) -> int:
    import subprocess
    import sys
    from pathlib import Path

    candidates = [
        Path(__file__).resolve().parents[2] / "dashboard" / "app.py",
        Path.cwd() / "dashboard" / "app.py",
        Path("/app/dashboard/app.py"),
        Path(__file__).resolve().parents[1] / "dashboard" / "app.py",
    ]
    app_path = next((p for p in candidates if p.exists()), candidates[0])
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(args.port),
        "--server.address",
        args.host,
        "--server.headless",
        "true",
    ]
    raise SystemExit(subprocess.call(cmd))


def run_queue(args: argparse.Namespace) -> int:
    from onpage_seo.cms import apply_suggestion, approve_suggestion, reject_suggestion

    settings = load_settings()
    store = open_store(settings.database_url)
    store.ensure_schema()

    if args.queue_command == "list":
        rows = store.list_suggestions(status=args.status, job_id=args.job_id, limit=200)
        _write_out({"count": len(rows), "suggestions": rows}, args.out)
        return 0

    if args.queue_command == "approve":
        item = approve_suggestion(store, args.suggestion_id, actor=args.actor)
        _write_out(item, None)
        return 0

    if args.queue_command == "reject":
        item = reject_suggestion(
            store, args.suggestion_id, actor=args.actor, reason=args.reason
        )
        _write_out(item, None)
        return 0

    if args.queue_command == "apply":
        if args.no_dry_run:
            dry_run = False
        elif args.dry_run:
            dry_run = True
        else:
            dry_run = settings.cms_dry_run_default
        result = apply_suggestion(
            store,
            args.suggestion_id,
            actor=args.actor,
            settings=settings,
            dry_run=dry_run,
        )
        _write_out(result, None)
        return 2 if result.get("error") else 0

    if args.queue_command == "audit":
        item = store.get_suggestion(args.suggestion_id)
        if not item:
            raise SystemExit(f"suggestion {args.suggestion_id} not found")
        _write_out(
            {"suggestion": item, "audit": store.list_audit_events(args.suggestion_id)},
            None,
        )
        return 0

    raise SystemExit(f"unknown queue command: {args.queue_command}")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "audit":
        raise SystemExit(run_audit(args))
    if args.command == "batch":
        raise SystemExit(run_batch(args))
    if args.command == "serve":
        raise SystemExit(run_serve(args))
    if args.command == "dashboard":
        raise SystemExit(run_dashboard(args))
    if args.command == "queue":
        raise SystemExit(run_queue(args))
    raise SystemExit(f"unknown command: {args.command}")



if __name__ == "__main__":
    main()
