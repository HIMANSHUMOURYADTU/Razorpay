"""Apply stored exception-memory patterns as Stage 'learned_rule' (before fuzzy/LLM)."""

from __future__ import annotations

import pandas as pd

from src.audit_trail import AuditTrail
from src.config import DEFAULT_CONFIG, MatchConfig
from src.exception_memory import ExceptionMemory, LearnedPattern
from src.match_utils import (
    amount_delta,
    date_delta_days,
    extract_vendor,
    reconstruct_gross,
    same_month,
)
from src.models import Match


def apply_learned_rules(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    memory: ExceptionMemory | None = None,
    config: MatchConfig = DEFAULT_CONFIG,
) -> list[Match]:
    memory = memory or ExceptionMemory()
    patterns = memory.list_patterns()
    if not patterns:
        return []
    matches: list[Match] = []
    for pattern in patterns:
        if pattern.taxonomy_code == "FEE_NET" and pattern.fee_rate_decimal() is not None:
            matches.extend(
                _apply_fee_net(df_a, df_b, audit, used_a, used_b, pattern, memory, config)
            )
        elif pattern.taxonomy_code == "TIME_LAG":
            matches.extend(
                _apply_time_lag(df_a, df_b, audit, used_a, used_b, pattern, memory, config)
            )
        elif pattern.taxonomy_code == "OOP":
            matches.extend(
                _apply_oop(df_a, df_b, audit, used_a, used_b, pattern, memory, config)
            )
    return matches


def _vendor_ok(description: str, pattern: LearnedPattern) -> bool:
    if not pattern.vendor:
        return True
    return pattern.vendor.casefold() in (description or "").casefold()


def _log_learned(
    audit: AuditTrail,
    match: Match,
    pattern: LearnedPattern,
    extra: dict,
) -> None:
    audit.log(
        stage="learned_rule",
        decision="match",
        reason=match.reason,
        record_ids={"txn_id": match.txn_id, "ledger_id": match.ledger_id},
        confidence=match.confidence,
        taxonomy_code=pattern.taxonomy_code,
        extra={"pattern_id": pattern.pattern_id, "rule": pattern.rule, **extra},
    )


def _apply_fee_net(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    pattern: LearnedPattern,
    memory: ExceptionMemory,
    config: MatchConfig,
) -> list[Match]:
    rate = pattern.fee_rate_decimal()
    assert rate is not None
    matches: list[Match] = []
    for _, a in df_a.iterrows():
        txn_id = a["txn_id"]
        if txn_id in used_a:
            continue
        if not _vendor_ok(str(a["description"]), pattern):
            continue
        gross = reconstruct_gross(a["amount"], rate)
        for _, b in df_b.iterrows():
            ledger_id = b["ledger_id"]
            if ledger_id in used_b:
                continue
            if a["order_ref_norm"] != b["order_ref_norm"]:
                continue
            if config.require_same_currency and a["currency"] != b["currency"]:
                continue
            if amount_delta(gross, b["amount"]) > config.fee_amount_tolerance:
                continue
            used_a.add(txn_id)
            used_b.add(ledger_id)
            memory.increment(pattern.pattern_id)
            delta = amount_delta(a["amount"], b["amount"])
            days = date_delta_days(a["settlement_date"], b["posting_date"])
            reason = (
                f"Learned rule {pattern.pattern_id} ({pattern.rule}): reconstructed gross "
                f"{gross} from net {a['amount']} at {rate * 100}% fee matches ledger {b['amount']}."
            )
            match = Match(
                txn_id=txn_id,
                ledger_id=ledger_id,
                stage="learned_rule",
                confidence=config.learned_rule_confidence,
                reason=reason,
                amount_delta=delta,
                date_delta_days=days,
                order_ref=a["order_ref"],
                taxonomy_code="FEE_NET",
            )
            matches.append(match)
            _log_learned(audit, match, pattern, {"reconstructed_gross": str(gross), "fee_rate": str(rate)})
            break
    return matches


def _apply_time_lag(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    pattern: LearnedPattern,
    memory: ExceptionMemory,
    config: MatchConfig,
) -> list[Match]:
    window = pattern.date_window_days or 14
    matches: list[Match] = []
    for _, a in df_a.iterrows():
        txn_id = a["txn_id"]
        if txn_id in used_a:
            continue
        if not _vendor_ok(str(a["description"]), pattern):
            continue
        for _, b in df_b.iterrows():
            ledger_id = b["ledger_id"]
            if ledger_id in used_b:
                continue
            if a["order_ref_norm"] != b["order_ref_norm"]:
                continue
            if config.require_same_currency and a["currency"] != b["currency"]:
                continue
            if amount_delta(a["amount"], b["amount"]) > config.stage1_amount_tolerance:
                continue
            days = date_delta_days(a["settlement_date"], b["posting_date"])
            if days > window:
                continue
            if (
                config.require_same_calendar_month
                and not pattern.ignore_period_guard
                and not same_month(a["settlement_date"], b["posting_date"])
            ):
                continue
            used_a.add(txn_id)
            used_b.add(ledger_id)
            memory.increment(pattern.pattern_id)
            reason = (
                f"Learned rule {pattern.pattern_id} ({pattern.rule}): amount match with "
                f"{days}d lag allowed by stored window {window}d."
            )
            match = Match(
                txn_id=txn_id,
                ledger_id=ledger_id,
                stage="learned_rule",
                confidence=config.learned_rule_confidence,
                reason=reason,
                amount_delta=amount_delta(a["amount"], b["amount"]),
                date_delta_days=days,
                order_ref=a["order_ref"],
                taxonomy_code="TIME_LAG",
            )
            matches.append(match)
            _log_learned(audit, match, pattern, {"date_window_days": window})
            break
    return matches


def _apply_oop(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    pattern: LearnedPattern,
    memory: ExceptionMemory,
    config: MatchConfig,
) -> list[Match]:
    """Labeled OOP: amount+order_ref match across an adjacent month is accepted."""
    matches: list[Match] = []
    for _, a in df_a.iterrows():
        txn_id = a["txn_id"]
        if txn_id in used_a:
            continue
        for _, b in df_b.iterrows():
            ledger_id = b["ledger_id"]
            if ledger_id in used_b:
                continue
            if a["order_ref_norm"] != b["order_ref_norm"]:
                continue
            if amount_delta(a["amount"], b["amount"]) > config.stage1_amount_tolerance:
                continue
            if same_month(a["settlement_date"], b["posting_date"]):
                continue
            used_a.add(txn_id)
            used_b.add(ledger_id)
            memory.increment(pattern.pattern_id)
            days = date_delta_days(a["settlement_date"], b["posting_date"])
            reason = (
                f"Learned rule {pattern.pattern_id} ({pattern.rule}): adjacent-period "
                f"amount match accepted for order_ref {a['order_ref']}."
            )
            match = Match(
                txn_id=txn_id,
                ledger_id=ledger_id,
                stage="learned_rule",
                confidence=config.learned_rule_confidence,
                reason=reason,
                amount_delta=amount_delta(a["amount"], b["amount"]),
                date_delta_days=days,
                order_ref=a["order_ref"],
                taxonomy_code="OOP",
            )
            matches.append(match)
            _log_learned(audit, match, pattern, {})
            break
    return matches
