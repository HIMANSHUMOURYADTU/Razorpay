"""Anthropic Messages API. Key: ANTHROPIC_API_KEY. Not the default (not free-tier)."""

from __future__ import annotations

import os

from src.llm_providers.base import LLMProvider, ProviderConfigError, parse_llm_json
from src.llm_providers.http_util import post_json

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise ProviderConfigError(
                "LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set. "
                "Copy .env.example to .env and add your key, or set LLM_PROVIDER to gemini|groq|ollama."
            )
        self.api_key = key
        self.model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    def classify_match(self, record_a: dict, record_b: dict, taxonomy_code: str = "") -> dict:
        prompt = self.build_prompt(record_a, record_b, taxonomy_code)
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": 256,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        data = post_json(url, payload, headers=headers, provider=self.name)
        try:
            text = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"anthropic returned an unexpected payload: {data!r}") from exc
        return parse_llm_json(text)
