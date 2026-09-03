"""Groq OpenAI-compatible chat completions. Key: GROQ_API_KEY."""

from __future__ import annotations

import os

from src.llm_providers.base import LLMProvider, ProviderConfigError, parse_llm_json
from src.llm_providers.http_util import post_json

DEFAULT_MODEL = "openai/gpt-oss-20b"


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self) -> None:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            raise ProviderConfigError(
                "LLM_PROVIDER=groq requires GROQ_API_KEY to be set. "
                "Copy .env.example to .env and add your key, or set LLM_PROVIDER to gemini|ollama|anthropic."
            )
        self.api_key = key
        self.model = os.getenv("GROQ_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    def classify_match(self, record_a: dict, record_b: dict, taxonomy_code: str = "") -> dict:
        prompt = self.build_prompt(record_a, record_b, taxonomy_code)
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only valid JSON matching the requested schema."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = post_json(url, payload, headers=headers, provider=self.name)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"groq returned an unexpected payload: {data!r}") from exc
        return parse_llm_json(text)
