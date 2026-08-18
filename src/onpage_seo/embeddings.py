"""In-job internal-link suggestions via lightweight embeddings.

ponytail: hashing / bag-of-words cosine over the current crawl corpus only.
Ceiling: no cross-job vector DB. Upgrade: pgvector / Chroma when multi-site.
"""

from __future__ import annotations

import math
import re
from typing import Any


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _vector(text: str) -> dict[str, float]:
    counts: dict[str, float] = {}
    for token in _tokenize(text):
        counts[token] = counts.get(token, 0.0) + 1.0
    return counts


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(value * b.get(key, 0.0) for key, value in a.items())
    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def suggest_internal_links(
    page: dict[str, Any],
    corpus: list[dict[str, Any]],
    *,
    limit: int = 3,
    min_score: float = 0.08,
) -> list[dict[str, str]]:
    source_url = page.get("final_url") or page.get("url")
    source_text = f"{page.get('title') or ''} {page.get('body_text') or ''}"
    source_vec = _vector(source_text)
    scored: list[tuple[float, dict[str, str]]] = []
    for other in corpus:
        if other.get("status") == "error":
            continue
        other_url = other.get("final_url") or other.get("url")
        if not other_url or other_url == source_url:
            continue
        other_text = f"{other.get('title') or ''} {other.get('body_text') or ''}"
        score = _cosine(source_vec, _vector(other_text))
        if score >= min_score:
            scored.append(
                (
                    score,
                    {
                        "to": str(other_url),
                        "reason": f"content similarity {score:.2f}",
                    },
                )
            )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in scored[:limit]]
