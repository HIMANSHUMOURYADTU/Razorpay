"""Orchestrate rule -> learned_rule -> fuzzy -> classifier -> LLM assist."""

from __future__ import annotations

import argparse
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

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

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

DEFAULT_AUDIT = REPO_ROOT / "audit_log.jsonl"


def run_pipeline(
    source_a_path: str | Path,
    source_b_path: str | Path,
    audit_path: str | Path = DEFAULT_AUDIT,
    config: MatchConfig = DEFAULT_CONFIG,
    *,
    memory: ExceptionMemory | None = None,
    llm_provider: LLMProvider | None = None,
    enable_llm: bool | None = None,
) -> MatchResult:
    if enable_llm is not None:
        config = replace(config, enable_llm=enable_llm)
    df_a = load_source_a(source_a_path)
    df_b = load_source_b(source_b_path)
    used_a: set[str] = set()
    used_b: set[str] = set()
    memory = memory or ExceptionMemory()
    result = MatchResult(source_a_count=len(df_a), source_b_count=len(df_b))
    t0 = time.perf_counter()
    stage_seconds: dict[str, float] = {}

    with AuditTrail(audit_path, reset=True) as audit:
        t = time.perf_counter()
        result.matches.extend(match_rules(df_a, df_b, audit, used_a, used_b, config))
        stage_seconds["rule"] = time.perf_counter() - t

        t = time.perf_counter()
        result.matches.extend(
            apply_learned_rules(df_a, df_b, audit, used_a, used_b, memory, config)
        )
        stage_seconds["learned_rule"] = time.perf_counter() - t

        t = time.perf_counter()
        result.matches.extend(match_fuzzy(df_a, df_b, audit, used_a, used_b, config))
        stage_seconds["fuzzy"] = time.perf_counter() - t

        t = time.perf_counter()
        classified_matches, exceptions = classify_exceptions(
            df_a, df_b, audit, used_a, used_b, config
        )
        result.matches.extend(classified_matches)
        stage_seconds["classifier"] = time.perf_counter() - t

        t = time.perf_counter()
        llm_matches, exceptions, skip_reason, provider_name = run_llm_assist(
            exceptions,
            df_a,
            df_b,
            audit,
            used_a,
            used_b,
            provider=llm_provider,
            config=config,
        )
        stage_seconds["llm_assist"] = time.perf_counter() - t
        result.matches.extend(llm_matches)
        result.exceptions = exceptions
        result.llm_skipped_reason = skip_reason
        result.llm_provider = provider_name
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


def print_report(result: MatchResult, batch: int | None = 1) -> None:
    from reports.report_generator import format_text_report

    print(format_text_report(result, batch=batch))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the finance-controller reconciliation pipeline.")
    parser.add_argument("--source-a", default=str(REPO_ROOT / "data" / "source_a.csv"))
    parser.add_argument("--source-b", default=str(REPO_ROOT / "data" / "source_b.csv"))
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    parser.add_argument("--batch", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM assist even if keys are set.")
    args = parser.parse_args()
    source_a, source_b = args.source_a, args.source_b
    if args.batch in (2, 3) and Path(args.source_a).name == "source_a.csv":
        source_a = str(REPO_ROOT / "data" / f"batch{args.batch}_source_a.csv")
        source_b = str(REPO_ROOT / "data" / f"batch{args.batch}_source_b.csv")
    result = run_pipeline(
        source_a,
        source_b,
        args.audit,
        enable_llm=False if args.no_llm else None,
    )
    print_report(result, batch=args.batch)
    print(f"\n  audit trail -> {args.audit}")


if __name__ == "__main__":
    main()
