"""Gemini free-tier provider (default). Key: GEMINI_API_KEY."""

from __future__ import annotations

import os

from src.llm_providers.base import LLMProvider, ProviderConfigError, parse_llm_json
from src.llm_providers.http_util import post_json

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise ProviderConfigError(
                "LLM_PROVIDER=gemini requires GEMINI_API_KEY to be set. "
                "Copy .env.example to .env and add your key, or set LLM_PROVIDER to groq|ollama|anthropic."
            )
        self.api_key = key
        self.model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    def classify_match(self, record_a: dict, record_b: dict, taxonomy_code: str = "") -> dict:
        prompt = self.build_prompt(record_a, record_b, taxonomy_code)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        data = post_json(url, payload, headers={}, provider=self.name)
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"gemini returned an unexpected payload: {data!r}") from exc
        return parse_llm_json(text)
