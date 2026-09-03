"""Stage 3 — assign exactly one taxonomy code to every residual record.

SPLIT combinations that sum-match are converted into matches (deterministic).
FEE_NET / TIME_LAG / PARTIAL / OOP / DUP are classified with a reason but are
NOT auto-matched: they stay on the exception list for human labeling or LLM.
"""

from __future__ import annotations

from itertools import combinations
from decimal import Decimal

import pandas as pd

from src.audit_trail import AuditTrail
from src.config import DEFAULT_CONFIG, MatchConfig
from src.match_utils import amount_delta, date_delta_days, fee_rate_match, money, same_month
from src.models import ExceptionRecord, Match


def classify_exceptions(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    config: MatchConfig = DEFAULT_CONFIG,
) -> tuple[list[Match], list[ExceptionRecord]]:
    matches: list[Match] = []
    matches.extend(_match_splits(df_a, df_b, audit, used_a, used_b, config))

    unmatched_a = df_a[~df_a["txn_id"].isin(used_a)].copy()
    unmatched_b = df_b[~df_b["ledger_id"].isin(used_b)].copy()
    a_by_ref = _group(unmatched_a, "order_ref_norm")
    b_by_ref = _group(unmatched_b, "order_ref_norm")
    all_refs = set(a_by_ref) | set(b_by_ref)

    tagged_a: set[str] = set()
    tagged_b: set[str] = set()
    exceptions: list[ExceptionRecord] = []

    # DUP leftovers: extra settlement whose order_ref+amount already matched a ledger row.
    exceptions.extend(
        _classify_duplicates(df_a, df_b, used_a, used_b, audit, tagged_a)
    )

    for ref in sorted(all_refs):
        a_rows = [r for r in a_by_ref.get(ref, []) if r["txn_id"] not in tagged_a and r["txn_id"] not in used_a]
        b_rows = [r for r in b_by_ref.get(ref, []) if r["ledger_id"] not in tagged_b and r["ledger_id"] not in used_b]
        if not a_rows and not b_rows:
            continue

        if len(a_rows) >= 2 and b_rows:
            # Residual split that did not sum-match still gets SPLIT, not a forced 1:1.
            reason = (
                f"Multiple unmatched settlements share order_ref {a_rows[0]['order_ref']} "
                "with a ledger row; treated as SPLIT, not 1:1 force-matched."
            )
            exceptions.extend(
                _emit_group("SPLIT", reason, a_rows, b_rows, audit, tagged_a, tagged_b)
            )
            continue

        if len(b_rows) >= 2 and a_rows:
            reason = (
                f"Multiple unmatched ledger rows share order_ref {b_rows[0]['order_ref']} "
                "with a settlement; treated as SPLIT, not 1:1 force-matched."
            )
            exceptions.extend(
                _emit_group("SPLIT", reason, a_rows, b_rows, audit, tagged_a, tagged_b)
            )
            continue

        if len(a_rows) == 1 and len(b_rows) == 1:
            code, reason = _classify_pair(a_rows[0], b_rows[0], config)
            exceptions.extend(
                _emit_group(code, reason, a_rows, b_rows, audit, tagged_a, tagged_b)
            )
            continue

        if len(a_rows) == 1 and not b_rows:
            row = a_rows[0]
            reason = (
                f"Settlement {row['txn_id']} (order_ref {row['order_ref']}) has no unmatched "
                "or plausible ledger counterpart."
            )
            exceptions.extend(
                _emit_group("UNRESOLVED", reason, a_rows, [], audit, tagged_a, tagged_b)
            )
            continue

        if len(b_rows) == 1 and not a_rows:
            row = b_rows[0]
            reason = (
                f"Ledger {row['ledger_id']} (order_ref {row['order_ref']}) has no unmatched "
                "or plausible settlement counterpart."
            )
            exceptions.extend(
                _emit_group("UNRESOLVED", reason, [], b_rows, audit, tagged_a, tagged_b)
            )
            continue

        reason = (
            f"order_ref {ref} has an unmatched residue that does not fit a known "
            "split/fee/timing pattern; left UNRESOLVED."
        )
        exceptions.extend(
            _emit_group("UNRESOLVED", reason, a_rows, b_rows, audit, tagged_a, tagged_b)
        )

    return matches, exceptions


def _group(df: pd.DataFrame, col: str) -> dict[str, list[pd.Series]]:
    out: dict[str, list[pd.Series]] = {}
    for _, row in df.iterrows():
        out.setdefault(row[col], []).append(row)
    return out


def _match_splits(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    config: MatchConfig,
) -> list[Match]:
    matches: list[Match] = []
    unmatched_a = df_a[~df_a["txn_id"].isin(used_a)]
    unmatched_b = df_b[~df_b["ledger_id"].isin(used_b)]
    a_by_ref = _group(unmatched_a, "order_ref_norm")
    b_by_ref = _group(unmatched_b, "order_ref_norm")

    for ref, a_rows in a_by_ref.items():
        b_rows = b_by_ref.get(ref, [])
        if len(a_rows) < 2 or not b_rows:
            continue
        found = _best_sum_combo(a_rows, b_rows, "A", config.split_amount_tolerance)
        if not found:
            continue
        combo_a, b_row, total, delta = found
        txn_ids = [r["txn_id"] for r in combo_a]
        if any(t in used_a for t in txn_ids) or b_row["ledger_id"] in used_b:
            continue
        for t in txn_ids:
            used_a.add(t)
        used_b.add(b_row["ledger_id"])
        days = min(date_delta_days(r["settlement_date"], b_row["posting_date"]) for r in combo_a)
        reason = (
            f"SPLIT sum-match on {combo_a[0]['order_ref']}: "
            + " + ".join(str(money(r['amount'])) for r in combo_a)
            + f" = {money(total)} vs ledger {money(b_row['amount'])} (delta {delta})."
        )
        match = Match(
            txn_id=txn_ids[0],
            ledger_id=b_row["ledger_id"],
            stage="classifier",
            confidence=0.92,
            reason=reason,
            amount_delta=delta,
            date_delta_days=days,
            order_ref=combo_a[0]["order_ref"],
            taxonomy_code="SPLIT",
            txn_ids=txn_ids,
            ledger_ids=[b_row["ledger_id"]],
        )
        matches.append(match)
        audit.log(
            stage="classifier",
            decision="match",
            reason=reason,
            record_ids={"txn_id": ",".join(txn_ids), "ledger_id": b_row["ledger_id"]},
            confidence=0.92,
            taxonomy_code="SPLIT",
            extra={"txn_ids": txn_ids, "sum": str(money(total))},
        )

    # Reverse: multiple ledger rows summing to one settlement.
    unmatched_a = df_a[~df_a["txn_id"].isin(used_a)]
    unmatched_b = df_b[~df_b["ledger_id"].isin(used_b)]
    a_by_ref = _group(unmatched_a, "order_ref_norm")
    b_by_ref = _group(unmatched_b, "order_ref_norm")
    for ref, b_rows in b_by_ref.items():
        a_rows = a_by_ref.get(ref, [])
        if len(b_rows) < 2 or not a_rows:
            continue
        found = _best_sum_combo(b_rows, a_rows, "B", config.split_amount_tolerance)
        if not found:
            continue
        combo_b, a_row, total, delta = found
        ledger_ids = [r["ledger_id"] for r in combo_b]
        if a_row["txn_id"] in used_a or any(x in used_b for x in ledger_ids):
            continue
        used_a.add(a_row["txn_id"])
        for x in ledger_ids:
            used_b.add(x)
        days = min(date_delta_days(a_row["settlement_date"], r["posting_date"]) for r in combo_b)
        reason = (
            f"SPLIT sum-match on {a_row['order_ref']}: ledger "
            + " + ".join(str(money(r['amount'])) for r in combo_b)
            + f" = {money(total)} vs settlement {money(a_row['amount'])} (delta {delta})."
        )
        match = Match(
            txn_id=a_row["txn_id"],
            ledger_id=ledger_ids[0],
            stage="classifier",
            confidence=0.92,
            reason=reason,
            amount_delta=delta,
            date_delta_days=days,
            order_ref=a_row["order_ref"],
            taxonomy_code="SPLIT",
            txn_ids=[a_row["txn_id"]],
            ledger_ids=ledger_ids,
        )
        matches.append(match)
        audit.log(
            stage="classifier",
            decision="match",
            reason=reason,
            record_ids={"txn_id": a_row["txn_id"], "ledger_id": ",".join(ledger_ids)},
            confidence=0.92,
            taxonomy_code="SPLIT",
            extra={"ledger_ids": ledger_ids, "sum": str(money(total))},
        )
    return matches


def _best_sum_combo(
    many: list[pd.Series],
    ones: list[pd.Series],
    many_side: str,
    tolerance: Decimal,
) -> tuple[list[pd.Series], pd.Series, Decimal, Decimal] | None:
    amount_key = "amount"
    best = None
    for size in range(2, min(4, len(many)) + 1):
        for combo in combinations(many, size):
            total = money(sum((money(r[amount_key]) for r in combo), Decimal("0")))
            for one in ones:
                delta = amount_delta(total, one[amount_key])
                if delta <= tolerance:
                    candidate = (list(combo), one, total, delta)
                    if best is None or delta < best[3]:
                        best = candidate
    return best


def _classify_duplicates(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    used_a: set[str],
    used_b: set[str],
    audit: AuditTrail,
    tagged_a: set[str],
) -> list[ExceptionRecord]:
    """An unmatched settlement that clones an already-matched (order_ref, amount) pair."""
    matched_keys: set[tuple[str, str]] = set()
    for _, a in df_a.iterrows():
        if a["txn_id"] not in used_a:
            continue
        matched_keys.add((a["order_ref_norm"], str(money(a["amount"]))))

    exceptions: list[ExceptionRecord] = []
    for _, a in df_a.iterrows():
        if a["txn_id"] in used_a or a["txn_id"] in tagged_a:
            continue
        key = (a["order_ref_norm"], str(money(a["amount"])))
        if key not in matched_keys:
            continue
        reason = (
            f"Duplicate settlement {a['txn_id']} repeats order_ref {a['order_ref']} "
            f"at amount {money(a['amount'])}; a counterpart is already matched. Not force-matched."
        )
        rec = ExceptionRecord(
            exception_id=f"exc-A-{a['txn_id']}",
            source="A",
            record_id=a["txn_id"],
            order_ref=a["order_ref"],
            amount=money(a["amount"]),
            taxonomy_code="DUP",
            reason=reason,
            counterpart_ids=[],
            description=str(a["description"]),
            currency=str(a["currency"]),
        )
        exceptions.append(rec)
        tagged_a.add(a["txn_id"])
        audit.log(
            stage="classifier",
            decision="exception",
            reason=reason,
            record_ids={"txn_id": a["txn_id"]},
            confidence=None,
            taxonomy_code="DUP",
        )
    return exceptions


def _classify_pair(a: pd.Series, b: pd.Series, config: MatchConfig) -> tuple[str, str]:
    delta = amount_delta(a["amount"], b["amount"])
    days = date_delta_days(a["settlement_date"], b["posting_date"])
    month_ok = same_month(a["settlement_date"], b["posting_date"])
    amt_a, amt_b = money(a["amount"]), money(b["amount"])

    if delta <= config.stage1_amount_tolerance and not month_ok:
        return (
            "OOP",
            f"Amount matches ({amt_a}) on {a['order_ref']} but posting "
            f"{pd.Timestamp(b['posting_date']).date()} and settlement "
            f"{pd.Timestamp(a['settlement_date']).date()} fall in different months.",
        )

    if delta <= config.stage1_amount_tolerance and days > config.stage2_date_window_days:
        return (
            "TIME_LAG",
            f"Amount matches ({amt_a}) on {a['order_ref']} but settlement is {days}d "
            f"from posting, outside the {config.stage2_date_window_days}d Stage 2 window.",
        )

    fee = fee_rate_match(amt_a, amt_b, config.fee_percentages, config.fee_amount_tolerance)
    if fee is not None:
        pct = (fee * 100).quantize(Decimal("0.01"))
        return (
            "FEE_NET",
            f"Settlement {amt_a} reconstructs to ledger {amt_b} at {pct}% platform fee "
            f"on {a['order_ref']}; not auto-matched until labeled or LLM-gated.",
        )
    fee_rev = fee_rate_match(amt_b, amt_a, config.fee_percentages, config.fee_amount_tolerance)
    if fee_rev is not None:
        pct = (fee_rev * 100).quantize(Decimal("0.01"))
        return (
            "FEE_NET",
            f"Ledger {amt_b} reconstructs to settlement {amt_a} at {pct}% platform fee "
            f"on {a['order_ref']}; not auto-matched until labeled or LLM-gated.",
        )

    if amt_b > 0:
        ratio = amt_a / amt_b
        if config.partial_ratio_min <= ratio <= config.partial_ratio_max:
            return (
                "PARTIAL",
                f"Settlement {amt_a} is {ratio:.0%} of ledger {amt_b} on {a['order_ref']} "
                "(partial refund / chargeback shape); not force-matched to the original gross.",
            )
        ratio_b = amt_b / amt_a if amt_a > 0 else Decimal("0")
        if config.partial_ratio_min <= ratio_b <= config.partial_ratio_max:
            return (
                "PARTIAL",
                f"Ledger {amt_b} is {ratio_b:.0%} of settlement {amt_a} on {a['order_ref']} "
                "(partial refund / chargeback shape); not force-matched.",
            )

    if delta <= max(config.stage2_amount_bands):
        return (
            "FX_ROUND",
            f"Shared order_ref {a['order_ref']} with amount delta {delta} looks like "
            "rounding/FX drift but did not pass Stage 2 gates.",
        )

    return (
        "UNRESOLVED",
        f"Shared order_ref {a['order_ref']} but amount delta {delta} is not fee, split, "
        "partial, rounding, or timing; left unresolved.",
    )


def _emit_group(
    code: str,
    reason: str,
    a_rows: list[pd.Series],
    b_rows: list[pd.Series],
    audit: AuditTrail,
    tagged_a: set[str],
    tagged_b: set[str],
) -> list[ExceptionRecord]:
    a_ids = [r["txn_id"] for r in a_rows]
    b_ids = [r["ledger_id"] for r in b_rows]
    out: list[ExceptionRecord] = []
    for row in a_rows:
        rec = ExceptionRecord(
            exception_id=f"exc-A-{row['txn_id']}",
            source="A",
            record_id=row["txn_id"],
            order_ref=row["order_ref"],
            amount=money(row["amount"]),
            taxonomy_code=code,
            reason=reason,
            counterpart_ids=b_ids,
            description=str(row["description"]),
            currency=str(row["currency"]),
        )
        out.append(rec)
        tagged_a.add(row["txn_id"])
        audit.log(
            stage="classifier",
            decision="exception",
            reason=reason,
            record_ids={"txn_id": row["txn_id"], "ledger_id": ",".join(b_ids) if b_ids else None},
            confidence=None,
            taxonomy_code=code,
        )
    for row in b_rows:
        rec = ExceptionRecord(
            exception_id=f"exc-B-{row['ledger_id']}",
            source="B",
            record_id=row["ledger_id"],
            order_ref=row["order_ref"],
            amount=money(row["amount"]),
            taxonomy_code=code,
            reason=reason,
            counterpart_ids=a_ids,
            description=str(row["description"]),
            currency=str(row["currency"]),
        )
        out.append(rec)
        tagged_b.add(row["ledger_id"])
        audit.log(
            stage="classifier",
            decision="exception",
            reason=reason,
            record_ids={"txn_id": ",".join(a_ids) if a_ids else None, "ledger_id": row["ledger_id"]},
            confidence=None,
            taxonomy_code=code,
        )
    return out
