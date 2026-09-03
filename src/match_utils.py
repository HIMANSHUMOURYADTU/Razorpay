"""Shared amount/date/vendor helpers used by every matching stage."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_EVEN

import pandas as pd

TWOPLACES = Decimal("0.01")


def money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_EVEN)


def amount_delta(a: Decimal | float | int | str, b: Decimal | float | int | str) -> Decimal:
    return abs(money(a) - money(b))


def date_delta_days(left, right) -> int:
    return abs((pd.Timestamp(left).normalize() - pd.Timestamp(right).normalize()).days)


def same_month(left, right) -> bool:
    l, r = pd.Timestamp(left), pd.Timestamp(right)
    return (l.year, l.month) == (r.year, r.month)


def extract_vendor(description: str) -> str:
    """Pull the merchant name out of our synthetic (and typical) description shapes."""
    text = (description or "").strip()
    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        if len(parts) >= 2:
            return parts[1]
    m = re.search(r"Ledger posting\s*-\s*([^|]+)", text, re.I)
    if m:
        return m.group(1).strip()
    return text


def fee_rate_match(
    net: Decimal,
    gross: Decimal,
    rates: tuple[Decimal, ...],
    tolerance: Decimal,
) -> Decimal | None:
    """Return the fee rate that reconstructs gross from net (or vice versa), else None."""
    net, gross = money(net), money(gross)
    if gross <= 0 or net <= 0:
        return None
    for rate in rates:
        if rate >= 1 or rate <= 0:
            continue
        expected_net = money(gross * (Decimal("1") - rate))
        if abs(expected_net - net) <= tolerance:
            return rate
        expected_gross = money(net / (Decimal("1") - rate))
        if abs(expected_gross - gross) <= tolerance:
            return rate
    return None


def reconstruct_gross(net: Decimal, rate: Decimal) -> Decimal:
    return money(money(net) / (Decimal("1") - rate))
