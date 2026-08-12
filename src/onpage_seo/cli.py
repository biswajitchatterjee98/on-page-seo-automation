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


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "audit":
        raise SystemExit(run_audit(args))
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
