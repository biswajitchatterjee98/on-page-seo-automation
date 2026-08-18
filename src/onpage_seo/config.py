"""Load thresholds and runtime settings from YAML + environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_THRESHOLDS = _ROOT / "config" / "thresholds.yaml"


def load_env_file(path: Path | None = None) -> None:
    env_path = path or (_ROOT / ".env")
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        with env_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k not in os.environ:
                    os.environ[k] = v


load_env_file()


@dataclass(frozen=True)
class Thresholds:
    rules_version: str
    title_min: int
    title_max: int
    meta_min: int
    meta_max: int
    intro_word_count: int
    min_word_count: int
    keyword_density_min_pct: float
    keyword_density_max_pct: float
    expect_schema: bool
    warn_score_ratio: float
    empty_body_word_threshold: int
    competitor_length_factor: float
    weights: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Thresholds:
        weights = {str(k): int(v) for k, v in (data.get("weights") or {}).items()}
        return cls(
            rules_version=str(data.get("rules_version", "1.0.0")),
            title_min=int(data["title_min"]),
            title_max=int(data["title_max"]),
            meta_min=int(data["meta_min"]),
            meta_max=int(data["meta_max"]),
            intro_word_count=int(data["intro_word_count"]),
            min_word_count=int(data["min_word_count"]),
            keyword_density_min_pct=float(data["keyword_density_min_pct"]),
            keyword_density_max_pct=float(data["keyword_density_max_pct"]),
            expect_schema=bool(data.get("expect_schema", False)),
            warn_score_ratio=float(data.get("warn_score_ratio", 0.5)),
            empty_body_word_threshold=int(data.get("empty_body_word_threshold", 20)),
            competitor_length_factor=float(data.get("competitor_length_factor", 0.8)),
            weights=weights,
        )


@dataclass(frozen=True)
class Settings:
    user_agent: str
    timeout_sec: float
    crawl_delay_sec: float
    ssrf_guard: bool
    thresholds_path: Path
    thresholds: Thresholds
    database_url: str | None
    api_token: str | None
    max_pages: int
    crawl_retries: int
    retry_backoff_sec: float
    llm_enabled: bool
    llm_api_key: str | None
    llm_base_url: str
    llm_model: str
    llm_max_calls_per_job: int
    llm_max_tokens: int
    internal_link_min_pages: int
    cms_provider: str
    wp_base_url: str | None
    wp_username: str | None
    wp_app_password: str | None
    cms_dry_run_default: bool


def load_thresholds(path: Path | None = None) -> Thresholds:
    resolved = path or Path(
        os.environ.get("ONPAGE_SEO_THRESHOLDS_PATH", str(_DEFAULT_THRESHOLDS))
    )
    if not resolved.is_absolute():
        resolved = (_ROOT / resolved).resolve()
    with resolved.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return Thresholds.from_dict(data)


def _llm_credentials() -> tuple[str | None, str, str]:
    """Groq env first; OPENAI_* kept for Ollama/Gemini-compatible endpoints."""
    groq_key = (os.environ.get("GROQ_API_KEY") or "").strip() or None
    if groq_key:
        return (
            groq_key,
            os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        )
    return (
        os.environ.get("OPENAI_API_KEY") or None,
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    )


def load_settings() -> Settings:
    thresholds_path = Path(
        os.environ.get("ONPAGE_SEO_THRESHOLDS_PATH", str(_DEFAULT_THRESHOLDS))
    )
    if not thresholds_path.is_absolute():
        thresholds_path = (_ROOT / thresholds_path).resolve()
    key, base_url, model = _llm_credentials()
    return Settings(
        user_agent=os.environ.get(
            "ONPAGE_SEO_USER_AGENT",
            "OnPageSEOBot/1.0 (+https://example.com/bot)",
        ),
        timeout_sec=float(os.environ.get("ONPAGE_SEO_REQUEST_TIMEOUT_SEC", "20")),
        crawl_delay_sec=float(os.environ.get("ONPAGE_SEO_CRAWL_DELAY_SEC", "1")),
        ssrf_guard=os.environ.get("ONPAGE_SEO_SSRF_GUARD", "1") not in {"0", "false", "False"},
        thresholds_path=thresholds_path,
        thresholds=load_thresholds(thresholds_path),
        database_url=os.environ.get("DATABASE_URL") or None,
        api_token=os.environ.get("ONPAGE_SEO_API_TOKEN") or None,
        max_pages=int(os.environ.get("ONPAGE_SEO_MAX_PAGES", "50")),
        crawl_retries=int(os.environ.get("ONPAGE_SEO_CRAWL_RETRIES", "3")),
        retry_backoff_sec=float(os.environ.get("ONPAGE_SEO_RETRY_BACKOFF_SEC", "0.5")),
        llm_enabled=os.environ.get("ONPAGE_SEO_LLM", "0") not in {"0", "false", "False"},
        llm_api_key=key,
        llm_base_url=base_url,
        llm_model=model,
        llm_max_calls_per_job=int(os.environ.get("ONPAGE_SEO_LLM_MAX_CALLS_PER_JOB", "20")),
        llm_max_tokens=int(os.environ.get("ONPAGE_SEO_LLM_MAX_TOKENS", "800")),
        internal_link_min_pages=int(os.environ.get("ONPAGE_SEO_INTERNAL_LINK_MIN_PAGES", "3")),
        cms_provider=os.environ.get("ONPAGE_SEO_CMS_PROVIDER", "null"),
        wp_base_url=os.environ.get("WP_BASE_URL") or None,
        wp_username=os.environ.get("WP_USERNAME") or None,
        wp_app_password=os.environ.get("WP_APP_PASSWORD") or None,
        cms_dry_run_default=os.environ.get("ONPAGE_SEO_CMS_DRY_RUN", "1")
        not in {"0", "false", "False"},
    )
