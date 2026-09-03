"""Draft solutions for leftovers. Never write Exception Memory from here."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd

from src.match_utils import extract_vendor, money
from src.models import ExceptionRecord

_FEE_IN_REASON = re.compile(r"at\s+(\d+(?:\.\d+)?)\s*%\s+platform fee", re.I)
_LAG_IN_REASON = re.compile(r"settlement is (\d+)d from posting", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def implied_fee_rate(net: Decimal, gross: Decimal) -> Decimal | None:
    if gross <= 0 or net <= 0 or net >= gross:
        return None
    rate = (Decimal("1") - (net / gross)).quantize(Decimal("0.0001"))
    if Decimal("0.005") <= rate <= Decimal("0.15"):
        return rate
    return None


def draft_proposals(
    exceptions: list[ExceptionRecord],
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
) -> list[dict[str, Any]]:
    a_lookup = {row["txn_id"]: row for _, row in df_a.iterrows()}
    b_lookup = {row["ledger_id"]: row for _, row in df_b.iterrows()}
    out: list[dict[str, Any]] = []
    seen_keys: set[tuple] = set()
    n = 0
    for exc in exceptions:
        if exc.source != "A":
            continue
        draft = _one(exc, a_lookup, b_lookup)
        if not draft:
            continue
        key = (draft["taxonomy_code"], draft.get("vendor"), draft.get("fee_rate"), draft.get("date_window_days"))
        if key in seen_keys and draft["taxonomy_code"] != "UNRESOLVED":
            # one CloudStack 2% card, not four
            continue
        seen_keys.add(key)
        n += 1
        draft["proposal_id"] = f"prop_{n:03d}"
        draft["status"] = "pending"
        draft["requires_human"] = True
        draft["created_at"] = _now()
        out.append(draft)
    return out


def _one(exc: ExceptionRecord, a_lookup: dict, b_lookup: dict) -> dict[str, Any] | None:
    vendor = extract_vendor(exc.description)
    base = {
        "exception_id": exc.exception_id,
        "record_id": exc.record_id,
        "order_ref": exc.order_ref,
        "amount": str(exc.amount),
        "taxonomy_code": exc.taxonomy_code,
        "evidence": exc.reason,
        "vendor": vendor or None,
        "fee_rate": None,
        "date_window_days": None,
        "ignore_period_guard": False,
        "proposed_rule": "",
        "agent_rationale": "",
        "executable": True,
    }

    if exc.taxonomy_code == "FEE_NET":
        pct = None
        m = _FEE_IN_REASON.search(exc.reason or "")
        if m:
            pct = Decimal(m.group(1))
            base["fee_rate"] = str((pct / Decimal("100")).quantize(Decimal("0.0001")))
        label = f"{pct:.2f}%" if pct is not None else "stated"
        name = vendor or "this vendor"
        base["proposed_rule"] = f"{name} settlements are net of {label} fee"
        base["agent_rationale"] = (
            "Classifier reconstructed a known fee table (2% / 2.36%) but did not auto-match. "
            "Operator must accept before Close 2 uses learned_rule."
        )
        return base

    if exc.taxonomy_code == "TIME_LAG":
        days = 14
        m = _LAG_IN_REASON.search(exc.reason or "")
        if m:
            days = max(14, int(m.group(1)) + 4)
        base["date_window_days"] = days
        base["vendor"] = None
        base["proposed_rule"] = f"Allow {days}-day settlement lag for delayed payouts"
        base["agent_rationale"] = (
            "Amount already matches; date is outside the 7-day fuzzy window. "
            "Widening the window is a policy choice — not an LLM match."
        )
        return base

    if exc.taxonomy_code == "OOP":
        base["ignore_period_guard"] = True
        base["vendor"] = None
        base["proposed_rule"] = "Allow adjacent-month settlement for out-of-period postings"
        base["agent_rationale"] = (
            "Same amount, different close month. Only a controller can accept period override."
        )
        return base

    if exc.taxonomy_code == "UNRESOLVED":
        a_row = a_lookup.get(exc.record_id)
        b_row = None
        for bid in exc.counterpart_ids or []:
            b_row = b_lookup.get(bid)
            if b_row is not None:
                break
        if a_row is not None and b_row is not None:
            rate = implied_fee_rate(money(a_row["amount"]), money(b_row["amount"]))
            if rate is not None:
                pct = (rate * 100).quantize(Decimal("0.01"))
                name = vendor or "this vendor"
                base["taxonomy_code"] = "FEE_NET"
                base["fee_rate"] = str(rate)
                base["proposed_rule"] = f"{name} settlements are net of {pct}% fee"
                base["agent_rationale"] = (
                    f"Out of box: amount delta is not in the 2%/2.36% table. "
                    f"Implied fee ≈ {pct}%. Drafted as FEE_NET for operator review — not auto-matched."
                )
                return base
        base["executable"] = False
        base["proposed_rule"] = "Escalate — no plausible counterpart. Do not fabricate a match."
        base["agent_rationale"] = (
            "Orphan or unexplained delta. Agent will not invent a ledger row. Operator investigates."
        )
        return base

    if exc.taxonomy_code in {"DUP", "PARTIAL", "SPLIT", "FX_ROUND"}:
        base["executable"] = False
        actions = {
            "DUP": "Escalate duplicate capture. Do not store a match rule that would glue the extra settlement.",
            "PARTIAL": "Hold net-of-refund. Matching the original gross would be a bad close.",
            "SPLIT": "Review split legs. Sum-match already ran; leftover is not 1:1.",
            "FX_ROUND": "Confirm rounding policy. Do not widen amount bands without a human.",
        }
        base["proposed_rule"] = actions[exc.taxonomy_code]
        base["agent_rationale"] = "No executable learned_rule for this taxonomy. Operator decides ops action only."
        return base

    return None
