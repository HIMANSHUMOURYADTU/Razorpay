"""Local Ollama provider. No API key. Default host http://localhost:11434."""

from __future__ import annotations

import os

from src.llm_providers.base import LLMProvider, parse_llm_json
from src.llm_providers.http_util import post_json

DEFAULT_MODEL = "llama3.2"
DEFAULT_HOST = "http://localhost:11434"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self) -> None:
        self.host = os.getenv("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    def classify_match(self, record_a: dict, record_b: dict, taxonomy_code: str = "") -> dict:
        prompt = self.build_prompt(record_a, record_b, taxonomy_code)
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        data = post_json(url, payload, headers={}, provider=self.name)
        text = ""
        if isinstance(data.get("message"), dict):
            text = data["message"].get("content") or ""
        if not text:
            text = data.get("response") or ""
        if not text:
            raise RuntimeError(f"ollama returned an unexpected payload: {data!r}")
        return parse_llm_json(text)
