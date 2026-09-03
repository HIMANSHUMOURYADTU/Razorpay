"""Stage 1 — deterministic exact match. Confidence is always 1.0."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from src.audit_trail import AuditTrail
from src.config import DEFAULT_CONFIG, MatchConfig
from src.models import Match


def _amount_delta(a: float, b: float) -> Decimal:
    return abs(Decimal(str(a)) - Decimal(str(b)))


def _date_delta_days(left, right) -> int:
    return abs((pd.Timestamp(left).normalize() - pd.Timestamp(right).normalize()).days)


def _same_month(left, right) -> bool:
    l, r = pd.Timestamp(left), pd.Timestamp(right)
    return (l.year, l.month) == (r.year, r.month)


def match_rules(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    config: MatchConfig = DEFAULT_CONFIG,
) -> list[Match]:
    """
    Pair unmatched A/B rows that share order_ref, currency, amount within
    stage1_amount_tolerance, and dates within stage1_date_window_days.

    1:1 assignment: once a ledger row is taken it cannot match again (duplicates
    stay unmatched for the exception classifier). Greedy by (date_delta, amount_delta, id).
    """
    matches: list[Match] = []
    b_by_ref: dict[str, list[int]] = {}
    for idx, row in df_b.iterrows():
        b_by_ref.setdefault(row["order_ref_norm"], []).append(idx)

    a_order = df_a.sort_values(["txn_id"]).index.tolist()
    for a_idx in a_order:
        a = df_a.loc[a_idx]
        txn_id = a["txn_id"]
        if txn_id in used_a:
            continue
        candidates: list[tuple[int, Decimal, int, str]] = []
        for b_idx in b_by_ref.get(a["order_ref_norm"], []):
            b = df_b.loc[b_idx]
            ledger_id = b["ledger_id"]
            if ledger_id in used_b:
                continue
            if config.require_same_currency and a["currency"] != b["currency"]:
                continue
            if config.require_same_calendar_month and not _same_month(
                a["settlement_date"], b["posting_date"]
            ):
                continue
            amt_delta = _amount_delta(a["amount"], b["amount"])
            if amt_delta > config.stage1_amount_tolerance:
                continue
            date_delta = _date_delta_days(a["settlement_date"], b["posting_date"])
            if date_delta > config.stage1_date_window_days:
                continue
            candidates.append((date_delta, amt_delta, b_idx, ledger_id))

        if not candidates:
            continue
        candidates.sort(key=lambda t: (t[0], t[1], t[3]))
        date_delta, amt_delta, _b_idx, ledger_id = candidates[0]
        used_a.add(txn_id)
        used_b.add(ledger_id)
        reason = (
            f"Exact match on order_ref {a['order_ref']}: amount delta {amt_delta} "
            f"(tolerance {config.stage1_amount_tolerance}) and {date_delta}d date gap "
            f"(window {config.stage1_date_window_days}d)."
        )
        match = Match(
            txn_id=txn_id,
            ledger_id=ledger_id,
            stage="rule",
            confidence=1.0,
            reason=reason,
            amount_delta=amt_delta,
            date_delta_days=date_delta,
            order_ref=a["order_ref"],
        )
        matches.append(match)
        audit.log(
            stage="rule",
            decision="match",
            reason=reason,
            record_ids={"txn_id": txn_id, "ledger_id": ledger_id},
            confidence=1.0,
            extra={
                "order_ref": a["order_ref"],
                "amount_delta": str(amt_delta),
                "date_delta_days": date_delta,
            },
        )
    return matches
