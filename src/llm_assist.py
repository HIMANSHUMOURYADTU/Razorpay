"""Stage 4 — confidence-gated LLM assist on classified residuals. Never a silent match."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from src.audit_trail import AuditTrail
from src.config import DEFAULT_CONFIG, MatchConfig
from src.llm_providers import get_provider
from src.llm_providers.base import LLMProvider, ProviderConfigError, RateLimitError
from src.match_utils import amount_delta, date_delta_days, money
from src.models import ExceptionRecord, LLM_RESOLVABLE, MEMORY_POLICY_CODES, Match

OFFICIAL_A = ["txn_id", "order_ref", "amount", "settlement_date", "description", "currency"]
OFFICIAL_B = ["ledger_id", "order_ref", "amount", "posting_date", "description", "currency"]


def _row_dict(row: pd.Series, columns: list[str]) -> dict:
    out = {}
    for col in columns:
        val = row[col]
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        elif col == "amount":
            val = str(money(val))
        else:
            val = str(val)
        out[col] = val
    return out


def run_llm_assist(
    exceptions: list[ExceptionRecord],
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    audit: AuditTrail,
    used_a: set[str],
    used_b: set[str],
    *,
    provider: LLMProvider | None = None,
    config: MatchConfig = DEFAULT_CONFIG,
) -> tuple[list[Match], list[ExceptionRecord], str | None, str | None]:
    """
    Returns (new_matches, remaining_exceptions, skip_reason, provider_name).
    skip_reason is set when the provider cannot start; remaining_exceptions then
    equal the input (still fully classified).
    """
    if not config.enable_llm:
        return [], exceptions, "LLM assist disabled by config", None

    if provider is None:
        try:
            provider = get_provider()
        except ProviderConfigError as exc:
            audit.log(
                stage="llm_assist",
                decision="skipped",
                reason=str(exc),
                record_ids={},
                confidence=None,
            )
            return [], exceptions, str(exc), None

    a_lookup = {row["txn_id"]: row for _, row in df_a.iterrows()}
    b_lookup = {row["ledger_id"]: row for _, row in df_b.iterrows()}
    by_ref: dict[str, list[ExceptionRecord]] = {}
    for exc in exceptions:
        by_ref.setdefault(exc.order_ref, []).append(exc)

    new_matches: list[Match] = []
    resolved_ids: set[str] = set()
    threshold = config.llm_confidence_threshold

    for _order_ref, group in by_ref.items():
        codes = {e.taxonomy_code for e in group}
        if codes <= {"UNRESOLVED"}:
            continue
        if "UNRESOLVED" in codes and len(codes) == 1:
            continue
        # Do not let the LLM 1:1-glue a split group.
        if "SPLIT" in codes:
            continue

        a_excs = [e for e in group if e.source == "A"]
        b_excs = [e for e in group if e.source == "B"]
        taxonomy = next((e.taxonomy_code for e in group if e.taxonomy_code in LLM_RESOLVABLE), group[0].taxonomy_code)

        if taxonomy not in LLM_RESOLVABLE:
            continue
        # Fee-net and date-lag are controller policy, not an LLM match.
        # Leaving them as exceptions is what puts CloudStack on the Agent queue.
        if taxonomy in MEMORY_POLICY_CODES:
            continue

        pairs: list[tuple[ExceptionRecord, pd.Series, pd.Series]] = []
        if a_excs and b_excs:
            for ae in a_excs:
                a_row = a_lookup.get(ae.record_id)
                if a_row is None:
                    continue
                for be in b_excs:
                    b_row = b_lookup.get(be.record_id)
                    if b_row is None:
                        continue
                    pairs.append((ae, a_row, b_row))
        elif a_excs and taxonomy == "DUP":
            # Counterpart ledger is already consumed; ask the model but never assign it twice.
            continue

        for ae, a_row, b_row in pairs:
            record_a = _row_dict(a_row, OFFICIAL_A)
            record_b = _row_dict(b_row, OFFICIAL_B)
            try:
                verdict = provider.classify_match(record_a, record_b, taxonomy_code=taxonomy)
            except RateLimitError as exc:
                # Free-tier TPM exhausted — log it, mark this exception with the reason,
                # and continue. The run completes; remaining pairs stay as exceptions.
                audit.log(
                    stage="llm_assist",
                    decision="rate_limited",
                    reason=str(exc),
                    record_ids={"txn_id": a_row["txn_id"], "ledger_id": b_row["ledger_id"]},
                    confidence=None,
                    taxonomy_code=taxonomy,
                    provider=provider.name,
                    extra={"input_a": record_a, "input_b": record_b},
                )
                ae.llm_reason = f"Rate limited ({provider.name}): {str(exc)[:120]}"
                # Stop sending more pairs this run to avoid more 429s
                return new_matches, [e for e in exceptions if e.exception_id not in resolved_ids], \
                    f"Rate limited after {len(new_matches)} LLM match(es). " \
                    "Wait ~1 min or toggle LLM off for a clean run.", provider.name
            except Exception as exc:  # noqa: BLE001 — log and keep the exception for humans
                audit.log(
                    stage="llm_assist",
                    decision="error",
                    reason=f"LLM call failed: {exc}",
                    record_ids={"txn_id": a_row["txn_id"], "ledger_id": b_row["ledger_id"]},
                    confidence=None,
                    taxonomy_code=taxonomy,
                    provider=provider.name,
                    extra={"input_a": record_a, "input_b": record_b},
                )
                ae.llm_reason = f"LLM call failed: {exc}"
                continue

            conf = float(verdict["confidence"])
            is_match = bool(verdict["is_match"])
            llm_reason = str(verdict["reason"])
            txn_id, ledger_id = a_row["txn_id"], b_row["ledger_id"]
            already_used = txn_id in used_a or ledger_id in used_b
            accept = is_match and conf >= threshold and not already_used

            decision = "match" if accept else "exception"
            if is_match and conf < threshold:
                gate_reason = (
                    f"LLM ({provider.name}) proposed a match at confidence {conf:.2f} "
                    f"below threshold {threshold:.2f}; left as {taxonomy} for human review. "
                    f"LLM reason: {llm_reason}"
                )
            elif is_match and already_used:
                gate_reason = (
                    f"LLM ({provider.name}) proposed a match but one record is already assigned; "
                    f"not force-matched. LLM reason: {llm_reason}"
                )
            elif accept:
                gate_reason = (
                    f"LLM ({provider.name}) match accepted at confidence {conf:.2f} "
                    f"(threshold {threshold:.2f}). {llm_reason}"
                )
            else:
                gate_reason = (
                    f"LLM ({provider.name}) rejected the pair at confidence {conf:.2f}. {llm_reason}"
                )

            audit.log(
                stage="llm_assist",
                decision=decision,
                reason=gate_reason,
                record_ids={"txn_id": txn_id, "ledger_id": ledger_id},
                confidence=conf,
                taxonomy_code=taxonomy,
                provider=provider.name,
                extra={
                    "input_a": record_a,
                    "input_b": record_b,
                    "llm_output": verdict,
                    "accepted": accept,
                    "threshold": threshold,
                },
            )

            if accept:
                used_a.add(txn_id)
                used_b.add(ledger_id)
                resolved_ids.add(ae.exception_id)
                for be in b_excs:
                    if be.record_id == ledger_id:
                        resolved_ids.add(be.exception_id)
                new_matches.append(
                    Match(
                        txn_id=txn_id,
                        ledger_id=ledger_id,
                        stage="llm_assisted",
                        confidence=conf,
                        reason=gate_reason,
                        amount_delta=amount_delta(a_row["amount"], b_row["amount"]),
                        date_delta_days=date_delta_days(a_row["settlement_date"], b_row["posting_date"]),
                        order_ref=str(a_row["order_ref"]),
                        taxonomy_code=taxonomy,
                        provider=provider.name,
                    )
                )
            else:
                ae.llm_reason = llm_reason
                ae.confidence = conf
                ae.reason = gate_reason

    remaining = [e for e in exceptions if e.exception_id not in resolved_ids]
    # Drop B-side rows that were the counterpart of an accepted match.
    remaining = [
        e
        for e in remaining
        if not (e.source == "B" and e.record_id in {m.ledger_id for m in new_matches})
    ]
    remaining = [
        e
        for e in remaining
        if not (e.source == "A" and e.record_id in {m.txn_id for m in new_matches})
    ]
    return new_matches, remaining, None, provider.name
