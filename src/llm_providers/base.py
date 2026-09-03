"""Shared prompt, JSON contract, and abstract LLMProvider."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

PROMPT_TEMPLATE = """You are a finance reconciliation assistant.
Decide whether these TWO records are the same economic event.
Use only the fields provided. Do not invent IDs or amounts.
The exception taxonomy already assigned to this residue is: {taxonomy_code}

Settlement record (source A):
{record_a}

Ledger record (source B):
{record_b}

Return a single JSON object and nothing else. No markdown, no code fences, no extra keys.
Schema:
{{"is_match": true or false, "confidence": <float 0.0 to 1.0>, "reason": "<one sentence>"}}
"""


def build_prompt(record_a: dict, record_b: dict, taxonomy_code: str = "") -> str:
    return PROMPT_TEMPLATE.format(
        taxonomy_code=taxonomy_code or "UNASSIGNED",
        record_a=json.dumps(record_a, default=str, ensure_ascii=True),
        record_b=json.dumps(record_b, default=str, ensure_ascii=True),
    )


def parse_llm_json(text: str) -> dict[str, Any]:
    """Parse model output into the strict {is_match, confidence, reason} contract."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("LLM returned an empty body")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise ValueError(f"LLM did not return JSON: {raw[:200]!r}")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM JSON must be an object")
    missing = [k for k in ("is_match", "confidence", "reason") if k not in data]
    if missing:
        raise ValueError(f"LLM JSON missing keys {missing}")
    extra = [k for k in data.keys() if k not in ("is_match", "confidence", "reason")]
    # Extra keys are ignored, not rejected — models sometimes add them.
    is_match = data["is_match"]
    if isinstance(is_match, str):
        is_match = is_match.strip().lower() in {"true", "yes", "1"}
    else:
        is_match = bool(is_match)
    confidence = float(data["confidence"])
    if confidence < 0 or confidence > 1:
        raise ValueError(f"LLM confidence {confidence} is outside [0, 1]")
    reason = str(data["reason"]).strip()
    if not reason:
        raise ValueError("LLM reason is empty")
    _ = extra
    return {"is_match": is_match, "confidence": confidence, "reason": reason}


class ProviderConfigError(RuntimeError):
    """Selected provider cannot start (usually a missing API key). Not a silent fallback."""


class RateLimitError(RuntimeError):
    """Provider throttled us after retries."""


class LLMProvider(ABC):
    name: str = "base"

    def build_prompt(self, record_a: dict, record_b: dict, taxonomy_code: str = "") -> str:
        return build_prompt(record_a, record_b, taxonomy_code)

    @abstractmethod
    def classify_match(self, record_a: dict, record_b: dict, taxonomy_code: str = "") -> dict:
        """Return {is_match: bool, confidence: float, reason: str}."""
