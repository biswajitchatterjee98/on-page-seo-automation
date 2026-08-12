"""Load thresholds and runtime settings from YAML + environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_THRESHOLDS = _ROOT / "config" / "thresholds.yaml"


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


def load_thresholds(path: Path | None = None) -> Thresholds:
    resolved = path or Path(
        os.environ.get("ONPAGE_SEO_THRESHOLDS_PATH", str(_DEFAULT_THRESHOLDS))
    )
    if not resolved.is_absolute():
        resolved = (_ROOT / resolved).resolve()
    with resolved.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return Thresholds.from_dict(data)


def load_settings() -> Settings:
    thresholds_path = Path(
        os.environ.get("ONPAGE_SEO_THRESHOLDS_PATH", str(_DEFAULT_THRESHOLDS))
    )
    if not thresholds_path.is_absolute():
        thresholds_path = (_ROOT / thresholds_path).resolve()
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
    )
