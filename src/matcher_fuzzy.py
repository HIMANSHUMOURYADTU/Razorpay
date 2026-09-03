"""Stage 2 — fuzzy / tolerant match on residuals. Never force-matches below threshold."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
from rapidfuzz import fuzz

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


def _amount_band_index(delta: Decimal, bands: tuple[Decimal, ...]) -> int | None:
    for i, band in enumerate(bands):
        if delta <= band:
            return i
    return None


def fuzzy_confidence(
    amount_band: int,
    date_delta_days: int,
    stage1_window: int,
    similarity: float,
) -> float:
    """
    Scale confidence by how many tolerance bands were needed.
    Tightest amount band starts at 0.90; each wider band drops 0.10.
    Extra days past the Stage 1 window drop 0.02 each.
    Description similarity is a small bump, not a substitute for amount/date.
    Capped at 0.99 — 1.0 is reserved for Stage 1.
    """
    conf = 0.90 - (0.10 * amount_band)
    if date_delta_days > stage1_window:
        conf -= 0.02 * (date_delta_days - stage1_window)
    if similarity >= 70:
        conf += 0.03
    elif similarity >= 50:
        conf += 0.01
    return round(min(max(conf, 0.0), 0.99), 4)


def match_fuzzy(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    config: MatchConfig = DEFAULT_CONFIG,
) -> list[Match]:
    """
    For records still unmatched after Stage 1: same order_ref, wider date window,
    stepped amount bands, description similarity as tiebreaker.

    A proposed pair is discarded (not matched) if confidence < stage2_min_confidence.
    """
    matches: list[Match] = []
    b_by_ref: dict[str, list[int]] = {}
    for idx, row in df_b.iterrows():
        if row["ledger_id"] in used_b:
            continue
        b_by_ref.setdefault(row["order_ref_norm"], []).append(idx)

    # Rank all surviving candidate pairs, then greedy-assign highest confidence first
    # so a close pair is not stolen by a looser one processed earlier in CSV order.
    scored: list[tuple[float, int, int, Decimal, int, float]] = []
    for a_idx, a in df_a.iterrows():
        if a["txn_id"] in used_a:
            continue
        for b_idx in b_by_ref.get(a["order_ref_norm"], []):
            b = df_b.loc[b_idx]
            if b["ledger_id"] in used_b:
                continue
            if config.require_same_currency and a["currency"] != b["currency"]:
                continue
            if config.require_same_calendar_month and not _same_month(
                a["settlement_date"], b["posting_date"]
            ):
                continue
            amt_delta = _amount_delta(a["amount"], b["amount"])
            band = _amount_band_index(amt_delta, config.stage2_amount_bands)
            if band is None:
                continue
            date_delta = _date_delta_days(a["settlement_date"], b["posting_date"])
            if date_delta > config.stage2_date_window_days:
                continue
            similarity = float(
                fuzz.token_set_ratio(str(a["description"]), str(b["description"]))
            )
            if similarity < config.stage2_min_description_similarity:
                continue
            conf = fuzzy_confidence(
                band, date_delta, config.stage1_date_window_days, similarity
            )
            if conf < config.stage2_min_confidence:
                continue
            scored.append((conf, a_idx, b_idx, amt_delta, date_delta, similarity))

    scored.sort(key=lambda t: (-t[0], t[3], t[4], df_a.loc[t[1], "txn_id"]))

    for conf, a_idx, b_idx, amt_delta, date_delta, similarity in scored:
        a = df_a.loc[a_idx]
        b = df_b.loc[b_idx]
        txn_id, ledger_id = a["txn_id"], b["ledger_id"]
        if txn_id in used_a or ledger_id in used_b:
            continue
        used_a.add(txn_id)
        used_b.add(ledger_id)
        band = _amount_band_index(amt_delta, config.stage2_amount_bands)
        band_limit = config.stage2_amount_bands[band] if band is not None else amt_delta
        reason = (
            f"Fuzzy match on order_ref {a['order_ref']}: amount delta {amt_delta} "
            f"(band {band_limit}), {date_delta}d date gap (window "
            f"{config.stage2_date_window_days}d), description similarity {similarity:.0f}; "
            f"confidence {conf} from tolerance bands used."
        )
        match = Match(
            txn_id=txn_id,
            ledger_id=ledger_id,
            stage="fuzzy",
            confidence=conf,
            reason=reason,
            amount_delta=amt_delta,
            date_delta_days=date_delta,
            description_similarity=similarity,
            order_ref=a["order_ref"],
        )
        matches.append(match)
        audit.log(
            stage="fuzzy",
            decision="match",
            reason=reason,
            record_ids={"txn_id": txn_id, "ledger_id": ledger_id},
            confidence=conf,
            extra={
                "order_ref": a["order_ref"],
                "amount_delta": str(amt_delta),
                "amount_band": str(band_limit),
                "date_delta_days": date_delta,
                "description_similarity": similarity,
            },
        )
    return matches
