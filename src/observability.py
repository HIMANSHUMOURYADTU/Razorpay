"""Wall-clock, LLM funnel, and *labeled estimates* for scale conversations.

Never mix these estimates with hidden-GT precision.
"""

from __future__ import annotations

from typing import Any

# Labeled estimates — not measured production billing.
# Groq openai/gpt-oss-20b on a short JSON classify is on the order of 0.1–0.3¢.
# Manual recon: ~12 seconds per settlement line in a typical close (ops interviews).
ESTIMATE_USD_PER_LLM_CALL = 0.0002
ESTIMATE_MANUAL_SECONDS_PER_RECORD = 12.0
ESTIMATE_LABEL = (
    "Estimates on this synthetic batch, not production invoices. "
    "Rules/fuzzy/classifier have ~zero token cost; LLM assist is the expensive tail."
)


def llm_funnel(audit_events: list[dict[str, Any]]) -> dict[str, Any]:
    offered: list[dict[str, Any]] = []
    for event in audit_events:
        if event.get("stage") != "llm_assist":
            continue
        if event.get("decision") not in {"match", "exception"}:
            continue
        extra = event.get("extra") or {}
        offered.append(
            {
                "decision": event.get("decision"),
                "confidence": event.get("confidence"),
                "taxonomy": event.get("taxonomy_code"),
                "reason": event.get("reason"),
                "record_ids": event.get("record_ids") or {},
                "accepted": bool(extra.get("accepted")),
                "threshold": extra.get("threshold", 0.75),
                "llm_output": extra.get("llm_output"),
            }
        )
    accepted = [r for r in offered if r["decision"] == "match"]
    refused = [r for r in offered if r["decision"] == "exception"]
    below = [
        r
        for r in refused
        if r.get("confidence") is not None and r.get("threshold") is not None
        and float(r["confidence"]) < float(r["threshold"])
    ]
    return {
        "calls": len(offered),
        "accepted": len(accepted),
        "refused": len(refused),
        "refused_below_threshold": len(below),
        "refusals": refused,
        "below_threshold_examples": below,
    }


def scale_card(
    *,
    source_a_count: int,
    wall_clock_seconds: float,
    llm_calls: int,
    matched_a: int,
) -> dict[str, Any]:
    n = max(source_a_count, 1)
    llm_rate = llm_calls / n
    sec_per = wall_clock_seconds / n
    per_1000_calls = 1000.0 * llm_rate
    per_1000_usd = per_1000_calls * ESTIMATE_USD_PER_LLM_CALL
    per_1000_sec = 1000.0 * sec_per
    manual_sec = ESTIMATE_MANUAL_SECONDS_PER_RECORD * n
    return {
        "label": ESTIMATE_LABEL,
        "usd_per_llm_call_estimate": ESTIMATE_USD_PER_LLM_CALL,
        "manual_seconds_per_record_estimate": ESTIMATE_MANUAL_SECONDS_PER_RECORD,
        "this_batch_wall_clock_seconds": round(wall_clock_seconds, 3),
        "this_batch_llm_calls": llm_calls,
        "this_batch_llm_usd_estimate": round(llm_calls * ESTIMATE_USD_PER_LLM_CALL, 6),
        "this_batch_manual_seconds_estimate": round(manual_sec, 1),
        "per_1000_records": {
            "llm_calls_estimate": round(per_1000_calls, 1),
            "llm_usd_estimate": round(per_1000_usd, 4),
            "controller_seconds_estimate": round(per_1000_sec, 1),
            "manual_seconds_estimate": round(ESTIMATE_MANUAL_SECONDS_PER_RECORD * 1000, 1),
        },
        "llm_share_of_records": round(llm_rate, 4),
        "auto_resolved_share": round(matched_a / n, 4),
    }
