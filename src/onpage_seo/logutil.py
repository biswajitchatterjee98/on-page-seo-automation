"""Structured JSON logging for job stages."""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key in ("job_id", "url", "stage", "duration_ms", "status", "error_code"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("onpage_seo")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


@contextmanager
def stage_timer(
    logger: logging.Logger,
    *,
    job_id: str,
    url: str,
    stage: str,
) -> Iterator[dict[str, Any]]:
    started = time.perf_counter()
    extra: dict[str, Any] = {"job_id": job_id, "url": url, "stage": stage}
    try:
        yield extra
        extra.setdefault("status", "ok")
    except Exception:
        extra.setdefault("status", "error")
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "%s finished",
            stage,
            extra={**extra, "duration_ms": duration_ms},
        )
