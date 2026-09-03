"""Factory: LLM_PROVIDER=gemini|groq|ollama|anthropic (default gemini)."""

from __future__ import annotations

import os

from src.llm_providers.anthropic_provider import AnthropicProvider
from src.llm_providers.base import LLMProvider, ProviderConfigError
from src.llm_providers.gemini_provider import GeminiProvider
from src.llm_providers.groq_provider import GroqProvider
from src.llm_providers.ollama_provider import OllamaProvider

PROVIDERS = {
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
}


def get_provider(name: str | None = None) -> LLMProvider:
    chosen = (name or os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    if chosen not in PROVIDERS:
        raise ProviderConfigError(
            f"Unknown LLM_PROVIDER={chosen!r}. Use one of: gemini, groq, ollama, anthropic."
        )
    return PROVIDERS[chosen]()
