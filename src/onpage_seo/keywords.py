"""Keyword sources — manual / JSON map / optional GSC adapter hook."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class KeywordSource(Protocol):
    def keywords_for_url(self, url: str) -> list[str]: ...


class ManualKeywordSource:
    def __init__(self, keywords: list[str] | None = None) -> None:
        self._keywords = list(keywords or [])

    def keywords_for_url(self, url: str) -> list[str]:
        return list(self._keywords)


class JsonKeywordSource:
    """Map of url → keywords from a JSON file (stand-in until GSC is wired)."""

    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("keyword JSON must be an object of url → string[]")
        self._map = {str(key): [str(item) for item in (value or [])] for key, value in data.items()}

    def keywords_for_url(self, url: str) -> list[str]:
        if url in self._map:
            return list(self._map[url])
        # ponytail: exact URL only; upgrade: normalize trailing slash / GSC query
        return []


class GscKeywordSource:
    """Google Search Console adapter placeholder.

    ponytail: no google-api dependency until credentials exist.
    Upgrade: implement Search Analytics query for page==url.
    """

    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(
            "GSC adapter not configured. Use JsonKeywordSource or ManualKeywordSource, "
            "or implement GscKeywordSource with Search Console credentials."
        )

    def keywords_for_url(self, url: str) -> list[str]:
        return []


def load_keyword_source(
    *,
    keywords: list[str] | None = None,
    json_path: str | None = None,
) -> KeywordSource:
    if json_path:
        return JsonKeywordSource(Path(json_path))
    return ManualKeywordSource(keywords)
