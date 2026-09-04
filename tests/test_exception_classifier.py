"""Classifier taxonomy tests plus LLM confidence gate."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from src.config import MatchConfig
from src.exception_classifier import classify_exceptions
from src.exception_memory import ExceptionMemory
from src.learned_rules import apply_learned_rules
from src.llm_assist import run_llm_assist
from src.matcher_rules import match_rules
from tests.conftest import ScriptedProvider, frame_a, frame_b, row_a, row_b


def _classify(audit, a_rows, b_rows, pre_match=True):
    df_a, df_b = frame_a(a_rows), frame_b(b_rows)
    used_a, used_b = set(), set()
    if pre_match:
        match_rules(df_a, df_b, audit, used_a, used_b)
    matches, exceptions = classify_exceptions(df_a, df_b, audit, used_a, used_b)
    return matches, exceptions, used_a, used_b, df_a, df_b


def test_dup_not_force_matched(audit):
    a_rows = [
        row_a("pay_1", "order_1", 500.00, "2026-08-10"),
        row_a("pay_2", "order_1", 500.00, "2026-08-10"),
    ]
    b_rows = [row_b("led_1", "order_1", 500.00, "2026-08-10")]
    _matches, exceptions, used_a, used_b, *_ = _classify(audit, a_rows, b_rows)
    assert len(used_b) == 1
    dup = [e for e in exceptions if e.taxonomy_code == "DUP"]
    assert len(dup) == 1
    assert dup[0].record_id in {"pay_1", "pay_2"}
    assert dup[0].reason


def test_split_sum_match(audit):
    a_rows = [
        row_a("pay_1", "order_1", 400.00, "2026-08-10", desc="Razorpay settlement | Acme | split 1"),
        row_a("pay_2", "order_1", 600.00, "2026-08-11", desc="Razorpay settlement | Acme | split 2"),
    ]
    b_rows = [row_b("led_1", "order_1", 1000.00, "2026-08-10")]
    matches, exceptions, used_a, *_ = _classify(audit, a_rows, b_rows, pre_match=True)
    split_matches = [m for m in matches if m.taxonomy_code == "SPLIT"]
    assert len(split_matches) == 1
    assert set(split_matches[0].txn_ids) == {"pay_1", "pay_2"}
    assert "pay_1" in used_a and "pay_2" in used_a
    assert not any(e.taxonomy_code == "SPLIT" and e.source == "A" for e in exceptions)


def test_fee_net_classified_not_auto_matched(audit):
    a_rows = [
        row_a(
            "pay_1",
            "order_1",
            980.00,
            "2026-08-11",
            desc="Razorpay settlement | CloudStack SaaS | order_1 | net of 2.00% fee",
        )
    ]
    b_rows = [
        row_b(
            "led_1",
            "order_1",
            1000.00,
            "2026-08-10",
            desc="Ledger posting - CloudStack SaaS | order_1 - gross",
        )
    ]
    matches, exceptions, used_a, *_ = _classify(audit, a_rows, b_rows)
    assert matches == []
    assert "pay_1" not in used_a
    codes = {e.taxonomy_code for e in exceptions}
    assert codes == {"FEE_NET"}
    assert all(e.reason for e in exceptions)


def test_time_lag_classified(audit):
    a_rows = [row_a("pay_1", "order_1", 800.00, "2026-08-20")]
    b_rows = [row_b("led_1", "order_1", 800.00, "2026-08-10")]
    _, exceptions, *_ = _classify(audit, a_rows, b_rows)
    assert {e.taxonomy_code for e in exceptions} == {"TIME_LAG"}


def test_oop_classified(audit):
    a_rows = [row_a("pay_1", "order_1", 800.00, "2026-08-02")]
    b_rows = [row_b("led_1", "order_1", 800.00, "2026-07-28")]
    _, exceptions, *_ = _classify(audit, a_rows, b_rows)
    assert {e.taxonomy_code for e in exceptions} == {"OOP"}


def test_partial_classified(audit):
    a_rows = [row_a("pay_1", "order_1", 800.00, "2026-08-12")]
    b_rows = [row_b("led_1", "order_1", 1000.00, "2026-08-10")]
    _, exceptions, *_ = _classify(audit, a_rows, b_rows)
    assert {e.taxonomy_code for e in exceptions} == {"PARTIAL"}


def test_fx_round_falls_through_when_outside_fuzzy_but_small(audit):
    # Classifier sees a leftover small delta that Stage 2 did not take (e.g. already used).
    # Direct classify with no pre-match: 0.20 delta, same date -> FX_ROUND if not fee/partial.
    a_rows = [row_a("pay_1", "order_1", 1000.20, "2026-08-10")]
    b_rows = [row_b("led_1", "order_1", 1000.00, "2026-08-10")]
    _, exceptions, *_ = _classify(audit, a_rows, b_rows, pre_match=False)
    assert {e.taxonomy_code for e in exceptions} == {"FX_ROUND"}


def test_unresolved_orphan(audit):
    a_rows = [row_a("pay_1", "order_orphan", 123.45, "2026-08-10")]
    b_rows = [row_b("led_1", "order_other", 999.00, "2026-08-10")]
    _, exceptions, *_ = _classify(audit, a_rows, b_rows)
    assert all(e.taxonomy_code == "UNRESOLVED" for e in exceptions)
    assert all(e.reason for e in exceptions)


def test_llm_below_threshold_not_force_matched(audit):
    a_rows = [
        row_a(
            "pay_1",
            "order_1",
            980.00,
            "2026-08-11",
            desc="Razorpay settlement | CloudStack SaaS | order_1",
        )
    ]
    b_rows = [
        row_b(
            "led_1",
            "order_1",
            1000.00,
            "2026-08-10",
            desc="Ledger posting - CloudStack SaaS | order_1",
        )
    ]
    matches, exceptions, used_a, used_b, df_a, df_b = _classify(audit, a_rows, b_rows)
    assert any(e.taxonomy_code == "FEE_NET" for e in exceptions)
    provider = ScriptedProvider(is_match=True, confidence=0.40, reason="looks related")
    cfg = MatchConfig(llm_confidence_threshold=0.75, enable_llm=True)
    llm_matches, remaining, skip, name = run_llm_assist(
        exceptions,
        df_a,
        df_b,
        audit,
        used_a,
        used_b,
        provider=provider,
        config=cfg,
    )
    assert llm_matches == []
    assert skip is None
    assert name == "scripted"
    assert "pay_1" not in used_a
    assert remaining
    assert remaining[0].taxonomy_code == "FEE_NET"
    # FEE_NET is memory policy — LLM is not asked, pair stays for the operator.
    assert provider.calls == []


def test_llm_never_auto_matches_fee_net(audit):
    a_rows = [
        row_a(
            "pay_1",
            "order_1",
            980.00,
            "2026-08-11",
            desc="Razorpay settlement | CloudStack SaaS | order_1 | net of 2.00% fee",
        )
    ]
    b_rows = [
        row_b(
            "led_1",
            "order_1",
            1000.00,
            "2026-08-10",
            desc="Ledger posting - CloudStack SaaS | order_1 - gross",
        )
    ]
    _, exceptions, used_a, used_b, df_a, df_b = _classify(audit, a_rows, b_rows)
    provider = ScriptedProvider(is_match=True, confidence=0.99, reason="obvious fee net")
    cfg = MatchConfig(llm_confidence_threshold=0.75, enable_llm=True)
    llm_matches, remaining, *_ = run_llm_assist(
        exceptions,
        df_a,
        df_b,
        audit,
        used_a,
        used_b,
        provider=provider,
        config=cfg,
    )
    assert llm_matches == []
    assert provider.calls == []
    assert "pay_1" not in used_a
    assert any(e.taxonomy_code == "FEE_NET" for e in remaining)


def test_llm_at_threshold_is_accepted(audit):
    a_rows = [row_a("pay_1", "order_1", 1000.20, "2026-08-10")]
    b_rows = [row_b("led_1", "order_1", 1000.00, "2026-08-10")]
    _, exceptions, used_a, used_b, df_a, df_b = _classify(audit, a_rows, b_rows, pre_match=False)
    assert any(e.taxonomy_code == "FX_ROUND" for e in exceptions)
    provider = ScriptedProvider(is_match=True, confidence=0.75, reason="rounding within policy")
    cfg = MatchConfig(llm_confidence_threshold=0.75, enable_llm=True)
    llm_matches, remaining, *_ = run_llm_assist(
        exceptions,
        df_a,
        df_b,
        audit,
        used_a,
        used_b,
        provider=provider,
        config=cfg,
    )
    assert len(llm_matches) == 1
    assert llm_matches[0].stage == "llm_assisted"
    assert llm_matches[0].provider == "scripted"
    assert "pay_1" in used_a


def test_learned_fee_net_rule_applies_before_fuzzy(audit, tmp_path: Path):
    mem = ExceptionMemory(tmp_path / "memory.json")
    mem.label_exception(
        "exc-A-demo",
        "CloudStack SaaS settlements are net of 2% fee",
        taxonomy_code="FEE_NET",
        vendor="CloudStack SaaS",
        fee_rate=Decimal("0.02"),
    )
    df_a = frame_a(
        [
            row_a(
                "pay_9",
                "order_9",
                1960.00,
                "2026-08-16",
                desc="Razorpay settlement | CloudStack SaaS | order_9 | net of 2.00% fee",
            )
        ]
    )
    df_b = frame_b(
        [
            row_b(
                "led_9",
                "order_9",
                2000.00,
                "2026-08-15",
                desc="Ledger posting - CloudStack SaaS | order_9 - gross",
            )
        ]
    )
    used_a, used_b = set(), set()
    matches = apply_learned_rules(df_a, df_b, audit, used_a, used_b, memory=mem)
    assert len(matches) == 1
    assert matches[0].stage == "learned_rule"
    assert matches[0].confidence == 0.95
    assert "pay_9" in used_a


def test_time_lag_label_does_not_bind_a_vendor(tmp_path: Path):
    mem = ExceptionMemory(tmp_path / "memory.json")
    mem.label_exception(
        "exc-A-demo",
        "Allow 14-day settlement lag for delayed payouts",
        taxonomy_code="TIME_LAG",
    )
    stored = next(p for p in mem.list_patterns() if p.taxonomy_code == "TIME_LAG")
    assert stored.vendor is None
    assert stored.date_window_days == 14


def test_memory_reads_utf8_bom_from_powershell(tmp_path: Path):
    path = tmp_path / "memory.json"
    path.write_bytes(b'\xef\xbb\xbf{"patterns": []}')
    assert ExceptionMemory(path).list_patterns() == []


def test_every_exception_has_code_and_reason(audit):
    a_rows = [
        row_a("pay_1", "order_1", 500.00, "2026-08-10"),
        row_a("pay_2", "order_1", 500.00, "2026-08-10"),
        row_a("pay_3", "order_gap", 77.00, "2026-08-10"),
    ]
    b_rows = [row_b("led_1", "order_1", 500.00, "2026-08-10")]
    _, exceptions, *_ = _classify(audit, a_rows, b_rows)
    assert exceptions
    for exc in exceptions:
        assert exc.taxonomy_code
        assert exc.reason.strip()
