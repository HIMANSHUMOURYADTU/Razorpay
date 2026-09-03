"""Which tool to run next.

The track is named *agent*, but a finance controller does not let a model skip
the deterministic floor. The charter is the policy. The agent *selects* among
tools under that charter and logs why. Matching math is unchanged.
"""

from __future__ import annotations

from typing import Any

# Execution order is the product. Skipping rule_match is not allowed.
CHARTER = (
    "Never skip the deterministic floor. Never auto-apply a new policy. "
    "LLM may only classify a residue pair. A human operator accepts solutions."
)

TOOLS = (
    "rule_match",
    "apply_memory",
    "fuzzy_match",
    "classify_exceptions",
    "llm_assist",
    "propose_policies",
    "stop",
)


def plan_next(state: dict[str, Any]) -> dict[str, str]:
    """Return {tool, why}. Deterministic charter — same close as the old pipeline."""
    ran: set[str] = set(state.get("ran") or [])
    enable_llm = bool(state.get("enable_llm"))
    unmatched_a = int(state.get("unmatched_a") or 0)
    memory_n = int(state.get("memory_n") or 0)

    if "rule_match" not in ran:
        return {
            "tool": "rule_match",
            "why": "Charter: exact order_ref + amount + date first (confidence 1.0). The model may not rewrite the easy majority.",
        }
    if "apply_memory" not in ran:
        extra = (
            f"{memory_n} accepted polic(y/ies) apply before fuzzy/LLM."
            if memory_n
            else "No accepted policies yet — tool still runs (no-op), then we continue."
        )
        return {
            "tool": "apply_memory",
            "why": f"Charter: human-validated memory before expensive tools. {extra}",
        }
    if "fuzzy_match" not in ran:
        return {
            "tool": "fuzzy_match",
            "why": "Residue after exact + memory: paise bands and description tie-break. Floor 0.70.",
        }
    if "classify_exceptions" not in ran:
        return {
            "tool": "classify_exceptions",
            "why": "Every leftover needs exactly one taxonomy before any generative assist. SPLIT may sum-match; FEE_NET is evidence, not a silent match.",
        }
    if enable_llm and "llm_assist" not in ran:
        return {
            "tool": "llm_assist",
            "why": "Gated classify_match on resolvable pairs only. Below 0.75 or 1:1 consumed → exception. JSON only.",
        }
    if not enable_llm and "llm_assist" not in ran:
        return {
            "tool": "llm_assist",
            "why": "LLM assist disabled — tool records skip and leaves classified exceptions untouched.",
        }
    if unmatched_a > 0 and "propose_policies" not in ran:
        return {
            "tool": "propose_policies",
            "why": "Out-of-policy residue: draft expected reason + solution for the operator. Do not execute.",
        }
    return {
        "tool": "stop",
        "why": "Close complete. New policies wait on human Accept / Edit / Reject.",
    }
