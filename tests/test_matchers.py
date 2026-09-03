"""Stage 1 / Stage 2 matcher tests. Nothing is force-matched below threshold."""

from __future__ import annotations

from src.config import DEFAULT_CONFIG, MatchConfig
from src.matcher_fuzzy import fuzzy_confidence, match_fuzzy
from src.matcher_rules import match_rules
from tests.conftest import frame_a, frame_b, row_a, row_b


def test_stage1_exact_match(audit):
    df_a = frame_a([row_a("pay_1", "order_1", 1000.00, "2026-08-10")])
    df_b = frame_b([row_b("led_1", "order_1", 1000.00, "2026-08-09")])
    used_a, used_b = set(), set()
    matches = match_rules(df_a, df_b, audit, used_a, used_b)
    assert len(matches) == 1
    assert matches[0].stage == "rule"
    assert matches[0].confidence == 1.0
    assert matches[0].txn_id == "pay_1"


def test_stage1_rejects_outside_date_window(audit):
    df_a = frame_a([row_a("pay_1", "order_1", 1000.00, "2026-08-20")])
    df_b = frame_b([row_b("led_1", "order_1", 1000.00, "2026-08-10")])
    matches = match_rules(df_a, df_b, audit, set(), set())
    assert matches == []


def test_stage1_rejects_different_month_even_if_amount_matches(audit):
    df_a = frame_a([row_a("pay_1", "order_1", 1000.00, "2026-08-02")])
    df_b = frame_b([row_b("led_1", "order_1", 1000.00, "2026-07-28")])
    matches = match_rules(df_a, df_b, audit, set(), set())
    assert matches == []


def test_stage1_one_to_one_leaves_duplicate_unmatched(audit):
    df_a = frame_a(
        [
            row_a("pay_1", "order_1", 500.00, "2026-08-10"),
            row_a("pay_2", "order_1", 500.00, "2026-08-10"),
        ]
    )
    df_b = frame_b([row_b("led_1", "order_1", 500.00, "2026-08-10")])
    used_a, used_b = set(), set()
    matches = match_rules(df_a, df_b, audit, used_a, used_b)
    assert len(matches) == 1
    leftover = {"pay_1", "pay_2"} - used_a
    assert len(leftover) == 1


def test_fuzzy_matches_paise_drift(audit):
    df_a = frame_a([row_a("pay_1", "order_1", 7750.05, "2026-08-10")])
    df_b = frame_b([row_b("led_1", "order_1", 7750.00, "2026-08-10")])
    used_a, used_b = set(), set()
    matches = match_fuzzy(df_a, df_b, audit, used_a, used_b)
    assert len(matches) == 1
    assert matches[0].stage == "fuzzy"
    assert matches[0].confidence < 1.0
    assert matches[0].confidence >= DEFAULT_CONFIG.stage2_min_confidence


def test_fuzzy_does_not_match_fee_sized_gap(audit):
    df_a = frame_a([row_a("pay_1", "order_1", 980.00, "2026-08-10")])
    df_b = frame_b([row_b("led_1", "order_1", 1000.00, "2026-08-10")])
    matches = match_fuzzy(df_a, df_b, audit, set(), set())
    assert matches == []


def test_fuzzy_confidence_below_threshold_is_discarded(audit):
    # Confidence formula with a huge date gap would drop below 0.70; window also rejects.
    conf = fuzzy_confidence(amount_band=2, date_delta_days=20, stage1_window=3, similarity=40)
    assert conf < DEFAULT_CONFIG.stage2_min_confidence
    df_a = frame_a([row_a("pay_1", "order_1", 1000.00, "2026-08-25")])
    df_b = frame_b([row_b("led_1", "order_1", 1000.90, "2026-08-10")])
    tight = MatchConfig(stage2_min_confidence=0.99, stage2_date_window_days=20)
    matches = match_fuzzy(df_a, df_b, audit, set(), set(), config=tight)
    assert matches == []
