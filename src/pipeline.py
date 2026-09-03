"""Orchestrate the Finance Controller agent (tools + operator). Matching math is unchanged."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from src.agent.orchestrator import DEFAULT_AUDIT, run_controller_agent
from src.config import DEFAULT_CONFIG, MatchConfig
from src.exception_memory import ExceptionMemory
from src.llm_providers.base import LLMProvider
from src.models import MatchResult

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


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
    """Public API — same tools, same order, agent loop + policy proposals."""
    return run_controller_agent(
        source_a_path,
        source_b_path,
        audit_path,
        config,
        memory=memory,
        llm_provider=llm_provider,
        enable_llm=enable_llm,
    )


def print_report(result: MatchResult, batch: int | None = 1) -> None:
    from reports.report_generator import format_text_report

    print(format_text_report(result, batch=batch))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the finance-controller agent.")
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
    if result.agent_trace:
        print("  agent tools :")
        for step in result.agent_trace:
            print(f"    {step['tool']:<22} unmatched_a={step.get('unmatched_a_after', '')}")


if __name__ == "__main__":
    main()
