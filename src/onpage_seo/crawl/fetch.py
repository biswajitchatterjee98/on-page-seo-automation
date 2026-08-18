"""Fetch pages with robots, SSRF, and optional Playwright fallback."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from onpage_seo.config import Settings
from onpage_seo.crawl.extract import extract_page
from onpage_seo.security.ssrf import SsrfBlockedError, assert_url_safe, resolve_redirect_url

logger = logging.getLogger("onpage_seo")

_SOFT_404_MARKERS = ("404", "not found", "page not found", "does not exist")
_MAX_REDIRECTS = 5
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _error(url: str, error_code: str, detail: str) -> dict[str, Any]:
    return {
        "url": url,
        "status": "error",
        "error_code": error_code,
        "detail": detail,
        "fetched_at": _utc_now(),
    }


def _robots_allowed(
    url: str,
    user_agent: str,
    timeout_sec: float,
    *,
    enforce_ssrf: bool,
) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    if enforce_ssrf:
        try:
            assert_url_safe(robots_url)
        except SsrfBlockedError:
            return False
    parser = RobotFileParser()
    try:
        response = requests.get(
            robots_url,
            timeout=timeout_sec,
            headers={"User-Agent": user_agent},
            allow_redirects=False,
        )
        if response.status_code >= 400:
            # ponytail: missing/unreadable robots → allow; upgrade: cache + stricter policy
            return True
        parser.parse(response.text.splitlines())
    except requests.RequestException:
        return True
    return parser.can_fetch(user_agent, url)


def _looks_soft_404(title: str, word_count: int, http_status: int) -> bool:
    if http_status == 404:
        return True
    lowered = title.lower()
    return word_count < 80 and any(marker in lowered for marker in _SOFT_404_MARKERS)


def get_following_redirects(
    url: str,
    settings: Settings,
    *,
    enforce_ssrf: bool,
) -> tuple[str, str, int]:
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if enforce_ssrf:
            assert_url_safe(current)
        response = requests.get(
            current,
            timeout=settings.timeout_sec,
            headers={"User-Agent": settings.user_agent},
            allow_redirects=False,
        )
        status = int(response.status_code)
        if status in _REDIRECT_STATUSES:
            current = resolve_redirect_url(current, response.headers.get("Location") or "")
            continue
        return response.text, str(response.url or current), status
    raise SsrfBlockedError(f"too many redirects from {url}")


def _fetch_static(
    url: str,
    settings: Settings,
    *,
    enforce_ssrf: bool,
) -> tuple[str, str, int]:
    attempts = max(1, settings.crawl_retries)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            html, final_url, status = get_following_redirects(
                url, settings, enforce_ssrf=enforce_ssrf
            )
            if status in _RETRY_STATUSES and attempt + 1 < attempts:
                time.sleep(settings.retry_backoff_sec * (2**attempt))
                continue
            return html, final_url, status
        except SsrfBlockedError:
            raise
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(settings.retry_backoff_sec * (2**attempt))
    if last_error:
        raise last_error
    raise RuntimeError("fetch failed without response")


def _fetch_playwright(url: str, settings: Settings) -> tuple[str, str, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. pip install 'onpage-seo[playwright]' && playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=settings.user_agent)
            response = page.goto(url, wait_until="networkidle", timeout=int(settings.timeout_sec * 1000))
            html = page.content()
            final_url = page.url
            status = int(response.status) if response else 200
            return html, final_url, status
        finally:
            browser.close()


def _extracted_page(
    html: str,
    *,
    url: str,
    final_url: str,
    http_status: int,
    render_mode: str,
    primary_keyword: str | None,
) -> dict[str, Any]:
    page = extract_page(
        html,
        url=url,
        final_url=final_url,
        http_status=http_status,
        render_mode=render_mode,
        primary_keyword=primary_keyword,
    )
    page["fetched_at"] = _utc_now()
    return page


def crawl_url(
    url: str,
    settings: Settings,
    *,
    primary_keyword: str | None = None,
    render_mode: str = "auto",
    enforce_ssrf: bool | None = None,
    ignore_robots: bool = False,
) -> dict[str, Any]:
    """Crawl one URL → page JSON or typed error object."""
    use_ssrf = settings.ssrf_guard if enforce_ssrf is None else enforce_ssrf
    if use_ssrf:
        try:
            assert_url_safe(url)
        except SsrfBlockedError as exc:
            return _error(url, "ssrf_blocked", str(exc))

    if not ignore_robots and not _robots_allowed(
        url, settings.user_agent, settings.timeout_sec, enforce_ssrf=use_ssrf
    ):
        return _error(url, "robots_disallowed", "URL disallowed by robots.txt")

    mode = render_mode if render_mode != "auto" else "static"
    try:
        if mode == "playwright":
            html, final_url, status = _fetch_playwright(url, settings)
        else:
            html, final_url, status = _fetch_static(url, settings, enforce_ssrf=use_ssrf)
    except SsrfBlockedError as exc:
        return _error(url, "ssrf_blocked", str(exc))
    except requests.Timeout:
        return _error(url, "timeout", f"request exceeded {settings.timeout_sec}s")
    except requests.RequestException as exc:
        return _error(url, "http_error", str(exc))
    except RuntimeError as exc:
        return _error(url, "unknown", str(exc))

    if status >= 400:
        return _error(url, "http_error", f"HTTP {status}")

    page = _extracted_page(
        html,
        url=url,
        final_url=final_url,
        http_status=status,
        render_mode=mode,
        primary_keyword=primary_keyword,
    )

    threshold = settings.thresholds.empty_body_word_threshold
    if page["word_count"] < threshold and render_mode == "auto":
        try:
            html, final_url, status = _fetch_playwright(url, settings)
            page = _extracted_page(
                html,
                url=url,
                final_url=final_url,
                http_status=status,
                render_mode="playwright",
                primary_keyword=primary_keyword,
            )
        except Exception as exc:
            # ponytail: keep static extract if Playwright missing/fails; empty_body may still fire
            logger.info(
                "playwright fallback skipped",
                extra={
                    "url": url,
                    "stage": "playwright",
                    "status": "error",
                    "error_code": str(exc),
                },
            )

    if page["word_count"] < threshold:
        return _error(url, "empty_body", f"extracted fewer than {threshold} words")

    if _looks_soft_404(page["title"], page["word_count"], status):
        return _error(url, "http_error", "soft-404 heuristic matched")

    if settings.crawl_delay_sec > 0:
        time.sleep(settings.crawl_delay_sec)

    return page
