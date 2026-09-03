"""Match rate, precision vs hidden ground truth, cash impact, and the exception list."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.controller_policy import exposure_by_taxonomy, prioritize
from src.models import Match, MatchResult
from src.observability import llm_funnel, scale_card

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GT = REPO_ROOT / "data" / "ground_truth.csv"

PRECISION_NOTE = (
    "Precision is measured only on matched pairs — we buy a high number by refusing "
    "to guess on the rest, which is why the exception list matters as much as the match rate. "
    "Zero false-positive matches is the objective, not artificial 100% coverage."
)


def load_ground_truth(path: str | Path = DEFAULT_GT) -> list[dict[str, str]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _index_gt(rows: list[dict[str, str]], batch: int | None = None) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    a_to_group: dict[str, str] = {}
    b_to_group: dict[str, str] = {}
    group_tax: dict[str, str] = {}
    for row in rows:
        if batch is not None and str(row.get("batch")) != str(batch):
            continue
        group_tax[row["gt_group"]] = row["taxonomy"]
        for aid in [x for x in (row.get("source_a_ids") or "").split(",") if x]:
            a_to_group[aid] = row["gt_group"]
        for bid in [x for x in (row.get("source_b_ids") or "").split(",") if x]:
            b_to_group[bid] = row["gt_group"]
    return a_to_group, b_to_group, group_tax


def score_matches(
    matches: list[Match],
    gt_rows: list[dict[str, str]],
    batch: int | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    """Offline scoring only. Ground truth is never shown to the matcher."""
    a_to_group, b_to_group, group_tax = _index_gt(gt_rows, batch)
    selected = [m for m in matches if stage is None or m.stage == stage]
    true = 0
    false = 0
    by_tax: Counter[str] = Counter()
    false_pairs: list[dict[str, str]] = []
    for match in selected:
        # A multi-id SPLIT match is correct if every A id shares the B group's gt_group.
        b_groups = {b_to_group.get(bid) for bid in match.ledger_ids}
        a_groups = {a_to_group.get(aid) for aid in match.txn_ids}
        ok = len(a_groups) == 1 and a_groups == b_groups and None not in a_groups
        if ok:
            true += 1
            tax = group_tax.get(next(iter(a_groups)) or "", "")
            by_tax[tax] += 1
        else:
            false += 1
            false_pairs.append(
                {
                    "txn_ids": ",".join(match.txn_ids),
                    "ledger_ids": ",".join(match.ledger_ids),
                    "stage": match.stage,
                }
            )
    total = true + false
    precision = (true / total) if total else None
    return {
        "stage": stage or "all",
        "pairs": total,
        "true": true,
        "false": false,
        "precision": precision,
        "true_by_taxonomy": dict(by_tax),
        "false_pairs": false_pairs,
    }


def build_report(
    result: MatchResult,
    *,
    gt_path: str | Path | None = DEFAULT_GT,
    batch: int | None = 1,
) -> dict[str, Any]:
    a_by_stage = result.a_records_by_stage()
    pair_by_stage = result.by_stage()
    exception_codes = Counter(e.taxonomy_code for e in result.exceptions)
    gt_rows = load_ground_truth(gt_path) if gt_path else []
    llm_precision = None
    overall = None
    if gt_rows:
        overall = score_matches(result.matches, gt_rows, batch=batch)
        llm_precision = score_matches(
            result.matches, gt_rows, batch=batch, stage="llm_assisted"
        )
    a_exceptions = [e for e in result.exceptions if e.source == "A"]
    funnel = llm_funnel(result.audit_events)
    matched_a = len(result.matched_txn_ids())
    prec_pairs = (overall or {}).get("pairs") or 0
    prec_true = (overall or {}).get("true") or 0
    prec_false = (overall or {}).get("false") or 0
    return {
        "source_a_count": result.source_a_count,
        "source_b_count": result.source_b_count,
        "matched_a": matched_a,
        "matched_b": len(result.matched_ledger_ids()),
        "match_rate_a": result.match_rate_a,
        "match_rate_b": result.match_rate_b,
        "match_rate_a_label": f"{result.match_rate_a:.1%} ({matched_a}/{result.source_a_count})",
        "matched_pairs": len(result.matches),
        "a_records_by_stage": a_by_stage,
        "pairs_by_stage": pair_by_stage,
        "exception_count": len(result.exceptions),
        "exception_count_a": len(a_exceptions),
        "exceptions_by_taxonomy": dict(exception_codes),
        "exceptions": [e.to_dict() for e in result.exceptions],
        "prioritized_exceptions": prioritize(result.exceptions),
        "exposure_by_taxonomy": exposure_by_taxonomy(result.exceptions),
        "llm_provider": result.llm_provider,
        "llm_skipped_reason": result.llm_skipped_reason,
        "offline_scoring": overall,
        "llm_precision": llm_precision,
        "llm_matches_by_provider": _provider_counts(result.matches),
        "llm_funnel": funnel,
        "precision_note": PRECISION_NOTE,
        "precision_label": (
            f"{(overall['precision'] * 100):.0f}% ({prec_true}/{prec_pairs} matched pairs, {prec_false} false-positive)"
            if overall and overall.get("precision") is not None
            else "n/a (no hidden GT for this batch)"
        ),
        "financial": {
            "currency": "INR",
            "settlement_value": str(result.amount_total_a),
            "settlement_value_inr": float(result.amount_total_a),
            "reconciled_value": str(result.amount_matched_a),
            "reconciled_value_inr": float(result.amount_matched_a),
            "exposure": str(result.amount_exception_a),
            "exposure_inr": float(result.amount_exception_a),
            "reconciled_share": (
                float(result.amount_matched_a / result.amount_total_a)
                if result.amount_total_a
                else 0.0
            ),
        },
        "stage_seconds": result.stage_seconds,
        "wall_clock_seconds": result.wall_clock_seconds,
        "scale": scale_card(
            source_a_count=result.source_a_count,
            wall_clock_seconds=result.wall_clock_seconds,
            llm_calls=funnel["calls"],
            matched_a=matched_a,
        ),
        "batch": batch,
        "batch_kind": "full_close",
        "batch_kind_note": (
            "Each demo batch is a full-size close (same trap mix, new IDs). "
            "Compare n as well as percentages — batch 2/3 are not a 18-row slice."
        ),
        "agent_charter": result.agent_charter,
        "agent_trace": result.agent_trace,
        "agent_proposals": result.agent_proposals,
        "pending_proposals": sum(1 for p in result.agent_proposals if p.get("status") == "pending"),
    }


def _provider_counts(matches: list[Match]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for match in matches:
        if match.stage == "llm_assisted" and match.provider:
            counts[match.provider] += 1
    return dict(counts)


def format_text_report(result: MatchResult, batch: int | None = 1) -> str:
    report = build_report(result, batch=batch)
    lines = [
        "=== Finance Controller reconciliation ===",
        f"  source A records : {report['source_a_count']}",
        f"  source B records : {report['source_b_count']}",
        f"  matched A        : {report['matched_a']}  ({report['match_rate_a']:.1%})  [{report['matched_a']}/{report['source_a_count']}]",
        f"  matched B        : {report['matched_b']}  ({report['match_rate_b']:.1%})",
        f"  matched pairs    : {report['matched_pairs']}",
        "  A records by stage:",
    ]
    for stage in ("rule", "learned_rule", "fuzzy", "classifier", "llm_assisted"):
        n = report["a_records_by_stage"].get(stage, 0)
        if n:
            lines.append(f"    {stage:<14} {n}")
    lines.append(f"  exceptions       : {report['exception_count']}")
    for code, n in sorted(report["exceptions_by_taxonomy"].items()):
        lines.append(f"    {code:<14} {n}")
    if report["llm_skipped_reason"]:
        lines.append(f"  LLM assist skipped: {report['llm_skipped_reason']}")
    elif report["llm_provider"]:
        lines.append(f"  LLM provider    : {report['llm_provider']}")
        for name, n in report["llm_matches_by_provider"].items():
            lines.append(f"    matches via {name}: {n}")
    scoring = report["offline_scoring"]
    if scoring:
        prec = scoring["precision"]
        prec_s = f"{prec:.1%}" if prec is not None else "n/a"
        lines.append(
            f"  offline precision (hidden GT, not used by matcher): "
            f"{scoring['true']}/{scoring['pairs']} matched pairs = {prec_s} "
            f"({scoring['false']} false-positive). {PRECISION_NOTE}"
        )
        llm = report["llm_precision"]
        if llm and llm["pairs"]:
            lp = llm["precision"]
            lp_s = f"{lp:.1%}" if lp is not None else "n/a"
            lines.append(
                f"  LLM-assisted precision: {llm['true']}/{llm['pairs']} = {lp_s}"
            )
    lines.append("  exception list:")
    for exc in result.exceptions:
        if exc.source != "A":
            continue
        lines.append(
            f"    {exc.exception_id}  {exc.taxonomy_code:<10}  {exc.record_id}  {exc.reason}"
        )
    return "\n".join(lines)
