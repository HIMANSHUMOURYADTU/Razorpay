"""Agent control layer — decisions *above* the matcher.

Does not change who matched. Turns each leftover into a controller action:
AUTO_RESOLVE (already matched) lives on Match rows; HOLD / ESCALATE on exceptions.

This is policy memory + cash control, not ML.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.models import ExceptionRecord, Match

# Integrity / missing counterpart → human must decide. Policy-shaped residue → hold.
ESCALATE_CODES = frozenset({"UNRESOLVED", "DUP"})
HOLD_CODES = frozenset({"FEE_NET", "TIME_LAG", "PARTIAL", "OOP", "SPLIT", "FX_ROUND"})

_ACTIONS = {
    "UNRESOLVED": "Escalate — no plausible counterpart. Do not fabricate a match.",
    "DUP": "Escalate — extra settlement on a consumed ledger row. Investigate duplicate capture.",
    "FEE_NET": "Hold — reconstruct gross at the vendor fee, then label into Exception Memory.",
    "TIME_LAG": "Hold — amount matches outside the date window. Widen the policy or wait for T+N.",
    "PARTIAL": "Hold — net-of-refund shape. Match only against the adjusted amount, never the gross.",
    "OOP": "Hold — posting and settlement fall in different close periods. Period decision required.",
    "SPLIT": "Hold — multiple legs share an order_ref and did not sum-match. Review split payout.",
    "FX_ROUND": "Hold — residual paise drift outside auto-match. Confirm FX/rounding policy.",
}

_SEVERITY_BASE = {
    "UNRESOLVED": "critical",
    "DUP": "high",
    "PARTIAL": "high",
    "OOP": "high",
    "FEE_NET": "medium",
    "TIME_LAG": "medium",
    "SPLIT": "medium",
    "FX_ROUND": "low",
}

_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def decision_for_taxonomy(code: str) -> str:
    if code in ESCALATE_CODES:
        return "ESCALATE"
    return "HOLD"


def _bump_severity(level: str, amount: Decimal) -> str:
    order = ["low", "medium", "high", "critical"]
    idx = order.index(level) if level in order else 1
    if amount >= Decimal("20000") and idx < 3:
        idx += 1
    if amount < Decimal("100") and level != "critical":
        idx = 0
    return order[idx]


def decorate_exception(exc: ExceptionRecord) -> dict[str, Any]:
    """Cash impact + controller action derived from existing exception fields."""
    amount = Decimal(str(exc.amount))
    code = exc.taxonomy_code
    severity = _bump_severity(_SEVERITY_BASE.get(code, "medium"), amount)
    decision = decision_for_taxonomy(code)
    llm_refused = bool(exc.llm_reason) and (
        (exc.confidence is not None and exc.confidence < 0.75)
        or "below threshold" in (exc.reason or "").lower()
        or "rejected the pair" in (exc.reason or "").lower()
    )
    return {
        **exc.to_dict(),
        "decision": decision,
        "requires_human": True,
        "recommended_action": _ACTIONS.get(code, "Hold for controller review."),
        "financial_impact": str(amount),
        "financial_impact_inr": float(amount),
        "severity": severity,
        "llm_refused": llm_refused,
        "evidence": [exc.reason] + ([f"LLM: {exc.llm_reason}"] if exc.llm_reason else []),
    }


def decorate_match(match: Match) -> dict[str, Any]:
    return {
        "decision": "AUTO_RESOLVE",
        "requires_human": False,
        "recommended_action": f"Accepted at stage={match.stage} confidence={match.confidence:.2f}.",
        "stage": match.stage,
        "confidence": match.confidence,
        "reason": match.reason,
        "order_ref": match.order_ref,
        "txn_ids": list(match.txn_ids),
        "ledger_ids": list(match.ledger_ids),
        "taxonomy_code": match.taxonomy_code,
        "provider": match.provider,
    }


def prioritize(exceptions: list[ExceptionRecord]) -> list[dict[str, Any]]:
    rows = [decorate_exception(e) for e in exceptions if e.source == "A"]
    rows.sort(
        key=lambda r: (
            _RANK.get(r["severity"], 9),
            -r["financial_impact_inr"],
            r["taxonomy_code"],
        )
    )
    return rows


def exposure_by_taxonomy(exceptions: list[ExceptionRecord]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for exc in exceptions:
        if exc.source != "A":
            continue
        bucket = out.setdefault(
            exc.taxonomy_code, {"count": 0, "amount": Decimal("0.00")}
        )
        bucket["count"] += 1
        bucket["amount"] += Decimal(str(exc.amount))
    return {
        code: {"count": v["count"], "amount": str(v["amount"]), "amount_inr": float(v["amount"])}
        for code, v in sorted(out.items())
    }
