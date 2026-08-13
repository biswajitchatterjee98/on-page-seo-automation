"""OpenAI-compatible chat client with hard call budget."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger("onpage_seo")

PROMPT_VERSION = "seo-llm-v1"


@dataclass
class LlmBudget:
    max_calls: int
    calls_used: int = 0

    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def consume(self) -> bool:
        if self.calls_used >= self.max_calls:
            return False
        self.calls_used += 1
        return True


@dataclass
class LlmClient:
    api_key: str
    base_url: str
    model: str
    max_tokens: int
    timeout_sec: float = 60.0
    budget: LlmBudget = field(default_factory=lambda: LlmBudget(max_calls=20))

    @property
    def model_version(self) -> str:
        return f"{self.model}@{PROMPT_VERSION}"

    def chat_json(self, *, system: str, user: str) -> dict[str, Any]:
        if not self.budget.consume():
            raise RuntimeError("LLM call budget exhausted for this job")
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key.strip() and self.api_key != "none":
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Include response_format for providers that support it
        if "ollama" not in self.base_url.lower():
            payload["response_format"] = {"type": "json_object"}

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=self.timeout_sec,
        )
        if response.status_code == 429:
            raise RuntimeError("LLM rate limited (429)")
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        # Clean up markdown code blocks if present (common in local open models)
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        return json.loads(content)
