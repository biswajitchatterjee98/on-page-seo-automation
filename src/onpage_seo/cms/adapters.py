"""CMS adapters — WordPress REST + dry-run / null."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import requests


class CmsError(RuntimeError):
    pass


class CmsAdapter(Protocol):
    name: str

    def apply(self, *, url: str, field: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply patch; return after-state dict. Must not be called for non-approved items."""
        ...


@dataclass
class NullCmsAdapter:
    """No-op adapter for tests / when CMS is not configured."""

    name: str = "null"

    def apply(self, *, url: str, field: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"url": url, "field": field, "value": payload.get("value"), "applied": False}


@dataclass
class WordPressCmsAdapter:
    base_url: str
    username: str
    app_password: str
    timeout_sec: float = 30.0
    name: str = "wordpress"

    def _auth(self) -> tuple[str, str]:
        return self.username, self.app_password

    def _api(self, path: str) -> str:
        return self.base_url.rstrip("/") + "/wp-json/wp/v2/" + path.lstrip("/")

    def _find_post(self, url: str) -> dict[str, Any]:
        slug = urlparse(url).path.strip("/").split("/")[-1]
        for resource in ("pages", "posts"):
            response = requests.get(
                self._api(resource),
                params={"slug": slug, "per_page": 1, "context": "edit"},
                auth=self._auth(),
                timeout=self.timeout_sec,
            )
            if response.status_code == 401:
                raise CmsError("WordPress auth failed")
            response.raise_for_status()
            items = response.json()
            if items:
                item = items[0]
                item["_resource"] = resource
                return item
        raise CmsError(f"no WordPress page/post found for slug {slug!r}")

    def apply(self, *, url: str, field: str, payload: dict[str, Any]) -> dict[str, Any]:
        post = self._find_post(url)
        resource = post["_resource"]
        post_id = int(post["id"])
        body: dict[str, Any] = {}
        if field == "title":
            body["title"] = payload.get("value")
        elif field == "meta_description":
            # ponytail: store in excerpt; upgrade: Yoast / RankMath meta keys when known
            body["excerpt"] = payload.get("value")
        elif field == "alt_text":
            raise CmsError("alt_text apply via WordPress media API not implemented in P4")
        else:
            raise CmsError(f"unsupported field: {field}")

        response = requests.post(
            self._api(f"{resource}/{post_id}"),
            json=body,
            auth=self._auth(),
            timeout=self.timeout_sec,
        )
        if response.status_code >= 400:
            raise CmsError(f"WordPress apply failed: HTTP {response.status_code} {response.text[:200]}")
        data = response.json()
        after_value = payload.get("value")
        if field == "title":
            rendered = (data.get("title") or {}).get("raw") or (data.get("title") or {}).get("rendered")
            after_value = rendered or after_value
        elif field == "meta_description":
            rendered = (data.get("excerpt") or {}).get("raw") or (data.get("excerpt") or {}).get("rendered")
            after_value = rendered or after_value
        return {
            "url": url,
            "field": field,
            "cms_id": post_id,
            "resource": resource,
            "value": after_value,
            "applied": True,
        }


def load_cms_adapter(
    *,
    provider: str | None,
    wp_base_url: str | None,
    wp_username: str | None,
    wp_app_password: str | None,
) -> CmsAdapter:
    provider = (provider or "null").lower()
    if provider in {"", "null", "none"}:
        return NullCmsAdapter()
    if provider in {"wordpress", "wp"}:
        if not (wp_base_url and wp_username and wp_app_password):
            raise CmsError("WordPress CMS requires WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD")
        return WordPressCmsAdapter(
            base_url=wp_base_url,
            username=wp_username,
            app_password=wp_app_password,
        )
    raise CmsError(f"unknown CMS provider: {provider}")
