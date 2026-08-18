"""CLI: onpage-seo audit | batch | serve."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from onpage_seo.config import load_settings
from onpage_seo.crawl import crawl_url
from onpage_seo.logutil import configure_logging, stage_timer
from onpage_seo.pipeline import JobConfig, run_job
from onpage_seo.report.html import render_batch_html
from onpage_seo.storage import open_store


def _add_shared_crawl_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Target keyword (repeatable). First keyword is primary.",
    )
    parser.add_argument(
        "--render-mode",
        choices=("auto", "static", "playwright"),
        default="auto",
        help="HTML fetch strategy (default: auto)",
    )
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Do not honor robots.txt (audited use only)",
    )
    parser.add_argument(
        "--no-ssrf-guard",
        action="store_true",
        help="Disable SSRF IP checks (local fixtures / trusted URLs only)",
    )
    parser.add_argument(
        "--job-id",
        default=None,
        help="Optional job id (default: generated UUID)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON output to this path (also prints to stdout)",
    )
    parser.add_argument(
        "--html-out",
        type=Path,
        default=None,
        help="Write optional HTML summary to this path",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable LLM enrichment for this run (requires GROQ_API_KEY)",
    )
    parser.add_argument(
        "--rules-only",
        action="store_true",
        help="Force rules-only mode (skip LLM even if ONPAGE_SEO_LLM=1)",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="onpage-seo",
        description="On-page SEO automation (P4: crawl → rules → LLM → queue → CMS apply)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="Crawl one URL and score with the rule engine")
    audit.add_argument("--url", required=True, help="Page URL to audit")
    _add_shared_crawl_flags(audit)
    audit.add_argument(
        "--competitor-word-count",
        type=float,
        default=None,
        help="Optional competitor average word count for length compare",
    )
    audit.add_argument(
        "--page-only",
        action="store_true",
        help="Emit crawler page JSON only (skip rules)",
    )

    batch = sub.add_parser("batch", help="Audit many URLs (list file and/or sitemap)")
    batch.add_argument("--url", action="append", default=[], help="Target URL (repeatable)")
    batch.add_argument("--url-file", type=Path, default=None, help="Text file of URLs, one per line")
    batch.add_argument("--sitemap-url", default=None, help="Sitemap or sitemap index URL")
    batch.add_argument(
        "--competitor-url",
        action="append",
        default=[],
        help="Competitor URL to crawl for average word count (repeatable)",
    )
    batch.add_argument(
        "--competitor-word-count",
        type=float,
        default=None,
        help="Skip competitor crawls; use this average directly",
    )
    batch.add_argument("--max-pages", type=int, default=None, help="Cap pages per job")
    batch.add_argument("--fail-fast", action="store_true", help="Stop after first crawl error")
    batch.add_argument(
        "--no-reuse",
        action="store_true",
        help="Do not reuse a prior completed job with the same config hash",
    )
    _add_shared_crawl_flags(batch)

    serve = sub.add_parser("serve", help="Run the Job API (FastAPI/uvicorn)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)

    dash = sub.add_parser("dashboard", help="Run the Streamlit trend dashboard")
    dash.add_argument("--port", type=int, default=8501)
    dash.add_argument("--host", default="0.0.0.0")

    queue = sub.add_parser("queue", help="Manage CMS suggestion approval queue")
    queue_sub = queue.add_subparsers(dest="queue_command", required=True)
    q_list = queue_sub.add_parser("list", help="List suggestions")
    q_list.add_argument("--status", default="pending")
    q_list.add_argument("--job-id", default=None)
    q_list.add_argument("--out", type=Path, default=None)

    q_approve = queue_sub.add_parser("approve", help="Approve a pending suggestion")
    q_approve.add_argument("suggestion_id", type=int)
    q_approve.add_argument("--actor", default="cli")

    q_reject = queue_sub.add_parser("reject", help="Reject a suggestion")
    q_reject.add_argument("suggestion_id", type=int)
    q_reject.add_argument("--actor", default="cli")
    q_reject.add_argument("--reason", default="")

    q_apply = queue_sub.add_parser("apply", help="Apply an approved suggestion to CMS")
    q_apply.add_argument("suggestion_id", type=int)
    q_apply.add_argument("--actor", default="cli")
    q_apply.add_argument("--dry-run", action="store_true", help="Audit only; do not mutate CMS")
    q_apply.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Force live CMS apply (overrides ONPAGE_SEO_CMS_DRY_RUN default)",
    )

    q_audit = queue_sub.add_parser("audit", help="Show audit trail for a suggestion")
    q_audit.add_argument("suggestion_id", type=int)

    return parser.parse_args(argv)


def _llm_flag(args: argparse.Namespace) -> bool | None:
    if args.rules_only:
        return False
    if args.llm:
        return True
    return None


def _write_out(payload: object, out: Path | None) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {out.resolve()}", file=sys.stderr)


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

    if args.page_only:
        with stage_timer(logger, job_id=job_id, url=url, stage="crawl"):
            page = crawl_url(
                url,
                settings,
                primary_keyword=keywords[0] if keywords else None,
                render_mode=args.render_mode,
                enforce_ssrf=not args.no_ssrf_guard,
                ignore_robots=args.ignore_robots,
            )
        _write_out(page, args.out)
        return 2 if page.get("status") == "error" else 0

    # Same path as batch/API: persist + enqueue accepted suggestions
    config = JobConfig(
        urls=[url],
        keywords=keywords,
        competitor_word_count=args.competitor_word_count,
        render_mode=args.render_mode,
        ignore_robots=args.ignore_robots,
        enforce_ssrf=not args.no_ssrf_guard,
        reuse_completed=False,
        enable_llm=_llm_flag(args),
    )
    store = open_store(settings.database_url)
    summary = run_job(config, settings, store=store, job_id=job_id)
    reports = summary.get("reports") or []
    payload = reports[0] if len(reports) == 1 else summary
    _write_out(payload, args.out)
    if args.html_out and reports:
        args.html_out.parent.mkdir(parents=True, exist_ok=True)
        from onpage_seo.report.html import render_report_html

        args.html_out.write_text(render_report_html(reports[0]), encoding="utf-8")

    status = summary.get("status")
    logger.info(
        "audit finished",
        extra={
            "job_id": job_id,
            "url": url,
            "stage": "done",
            "status": "ok" if status != "failed" else "error",
        },
    )
    if status == "failed":
        return 2
    if status == "completed_with_errors":
        return 1
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
