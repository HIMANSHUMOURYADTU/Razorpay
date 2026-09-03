"""Run the close as an agent loop over existing matching tools.

Tools call the same functions as before. The planner's charter preserves order,
so match rates and precision do not change.
"""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.agent.operator import ProposalQueue
from src.agent.planner import CHARTER, plan_next
from src.agent.proposals import draft_proposals
from src.audit_trail import AuditTrail
from src.config import DEFAULT_CONFIG, MatchConfig
from src.exception_classifier import classify_exceptions
from src.exception_memory import ExceptionMemory
from src.ingestion import load_source_a, load_source_b
from src.learned_rules import apply_learned_rules
from src.llm_assist import run_llm_assist
from src.llm_providers.base import LLMProvider
from src.matcher_fuzzy import match_fuzzy
from src.matcher_rules import match_rules
from src.models import MatchResult, UnmatchedRecord

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_AUDIT = REPO_ROOT / "audit_log.jsonl"


def _unmatched_a(df_a, used_a: set[str]) -> int:
    return int((~df_a["txn_id"].isin(used_a)).sum())


def run_controller_agent(
    source_a_path: str | Path,
    source_b_path: str | Path,
    audit_path: str | Path = DEFAULT_AUDIT,
    config: MatchConfig = DEFAULT_CONFIG,
    *,
    memory: ExceptionMemory | None = None,
    llm_provider: LLMProvider | None = None,
    enable_llm: bool | None = None,
    proposal_queue: ProposalQueue | None = None,
) -> MatchResult:
    if enable_llm is not None:
        config = replace(config, enable_llm=enable_llm)
    df_a = load_source_a(source_a_path)
    df_b = load_source_b(source_b_path)
    used_a: set[str] = set()
    used_b: set[str] = set()
    memory = memory or ExceptionMemory()
    queue = proposal_queue or ProposalQueue(Path(memory.path).parent / "agent_proposals.json")
    result = MatchResult(source_a_count=len(df_a), source_b_count=len(df_b))
    result.agent_charter = CHARTER
    t0 = time.perf_counter()
    stage_seconds: dict[str, float] = {}
    ran: set[str] = set()
    exceptions: list = []
    skip_reason = None
    provider_name = None

    with AuditTrail(audit_path, reset=True) as audit:
        safety = 0
        while safety < 12:
            safety += 1
            decision = plan_next(
                {
                    "ran": ran,
                    "enable_llm": config.enable_llm,
                    "unmatched_a": _unmatched_a(df_a, used_a),
                    "memory_n": len(memory.list_patterns()),
                }
            )
            tool = decision["tool"]
            why = decision["why"]
            before_m = len(result.matches)
            before_u = _unmatched_a(df_a, used_a)
            t = time.perf_counter()

            if tool == "stop":
                result.agent_trace.append(
                    {"tool": tool, "why": why, "resolved_a": 0, "unmatched_a_after": before_u, "seconds": 0.0}
                )
                audit.log(
                    stage="agent",
                    decision="stop",
                    reason=why,
                    record_ids={},
                    confidence=None,
                )
                break

            if tool == "rule_match":
                result.matches.extend(match_rules(df_a, df_b, audit, used_a, used_b, config))
                stage_seconds["rule"] = time.perf_counter() - t
            elif tool == "apply_memory":
                result.matches.extend(
                    apply_learned_rules(df_a, df_b, audit, used_a, used_b, memory, config)
                )
                stage_seconds["learned_rule"] = time.perf_counter() - t
            elif tool == "fuzzy_match":
                result.matches.extend(match_fuzzy(df_a, df_b, audit, used_a, used_b, config))
                stage_seconds["fuzzy"] = time.perf_counter() - t
            elif tool == "classify_exceptions":
                classified_matches, exceptions = classify_exceptions(
                    df_a, df_b, audit, used_a, used_b, config
                )
                result.matches.extend(classified_matches)
                result.exceptions = exceptions
                stage_seconds["classifier"] = time.perf_counter() - t
            elif tool == "llm_assist":
                llm_matches, exceptions, skip_reason, provider_name = run_llm_assist(
                    exceptions if exceptions else result.exceptions,
                    df_a,
                    df_b,
                    audit,
                    used_a,
                    used_b,
                    provider=llm_provider,
                    config=config,
                )
                result.matches.extend(llm_matches)
                result.exceptions = exceptions
                result.llm_skipped_reason = skip_reason
                result.llm_provider = provider_name
                stage_seconds["llm_assist"] = time.perf_counter() - t
            elif tool == "propose_policies":
                drafts = draft_proposals(result.exceptions, df_a, df_b)
                queue.replace_pending(drafts)
                result.agent_proposals = drafts
                stage_seconds["propose_policies"] = time.perf_counter() - t
                audit.log(
                    stage="agent",
                    decision="propose",
                    reason=f"{len(drafts)} draft policies queued for operator. None auto-applied.",
                    record_ids={},
                    confidence=None,
                    extra={"proposal_ids": [d["proposal_id"] for d in drafts]},
                )

            ran.add(tool)
            resolved = _unmatched_a(df_a, used_a)
            added = len(result.matches) - before_m
            elapsed = round(time.perf_counter() - t, 4)
            step = {
                "tool": tool,
                "why": why,
                "matches_added": added,
                "unmatched_a_before": before_u,
                "unmatched_a_after": resolved,
                "seconds": elapsed,
            }
            result.agent_trace.append(step)
            audit.log(
                stage="agent",
                decision="tool",
                reason=why,
                record_ids={"tool": tool},
                confidence=None,
                extra=step,
            )

        result.audit_events = list(audit.events)

    result.stage_seconds = {k: round(v, 4) for k, v in stage_seconds.items()}
    result.wall_clock_seconds = round(time.perf_counter() - t0, 4)
    matched_ids = result.matched_txn_ids()
    result.amount_total_a = Decimal(str(df_a["amount"].sum())).quantize(Decimal("0.01"))
    result.amount_matched_a = Decimal(
        str(df_a.loc[df_a["txn_id"].isin(matched_ids), "amount"].sum())
    ).quantize(Decimal("0.01"))
    result.amount_exception_a = Decimal(
        str(df_a.loc[~df_a["txn_id"].isin(matched_ids), "amount"].sum())
    ).quantize(Decimal("0.01"))

    for exc in result.exceptions:
        if not exc.taxonomy_code or not exc.reason:
            raise RuntimeError(
                f"Invariant violated: exception {exc.exception_id} missing taxonomy/reason"
            )

    result.unmatched_a = [
        UnmatchedRecord("A", e.record_id, e.order_ref, e.amount, e.reason, e.taxonomy_code)
        for e in result.exceptions
        if e.source == "A"
    ]
    result.unmatched_b = [
        UnmatchedRecord("B", e.record_id, e.order_ref, e.amount, e.reason, e.taxonomy_code)
        for e in result.exceptions
        if e.source == "B"
    ]

    memory.save_exceptions([e.to_dict() for e in result.exceptions])
    return result
