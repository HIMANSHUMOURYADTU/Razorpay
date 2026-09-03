from src.exception_memory import parse_resolution_rule
from src.llm_providers import get_provider
from src.llm_providers.base import ProviderConfigError, parse_llm_json


def test_parse_llm_json_strips_fences():
    raw = '```json\n{"is_match": true, "confidence": 0.8, "reason": "same order"}\n```'
    out = parse_llm_json(raw)
    assert out == {"is_match": True, "confidence": 0.8, "reason": "same order"}


def test_get_provider_unknown():
    try:
        get_provider("nope")
        assert False, "expected error"
    except ProviderConfigError as exc:
        assert "Unknown LLM_PROVIDER" in str(exc)


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    try:
        get_provider("gemini")
        assert False, "expected error"
    except ProviderConfigError as exc:
        assert "GEMINI_API_KEY" in str(exc)
        assert "groq" in str(exc)


def test_parse_rule_does_not_treat_settlement_lag_as_vendor():
    parsed = parse_resolution_rule("Allow 14-day settlement lag for delayed payouts")
    assert parsed.get("date_window_days") == 14
    assert "vendor" not in parsed


def test_parse_rule_extracts_fee_vendor():
    parsed = parse_resolution_rule("CloudStack SaaS settlements are net of 2% fee")
    assert parsed["vendor"] == "CloudStack SaaS"
    assert parsed["fee_rate"] * 100 == 2
