from decimal import Decimal
from pathlib import Path

from reports.report_generator import PRECISION_NOTE, build_report
from src.controller_policy import decision_for_taxonomy, decorate_exception, prioritize
from src.exception_memory import ExceptionMemory
from src.models import ExceptionRecord
from src.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def test_unresolved_escalates_and_ranks_first():
    rows = [
        ExceptionRecord("e1", "A", "pay_x", "o1", Decimal("48000"), "UNRESOLVED", "orphan"),
        ExceptionRecord("e2", "A", "pay_y", "o2", Decimal("1.20"), "FX_ROUND", "paise"),
        ExceptionRecord("e3", "A", "pay_z", "o3", Decimal("12000"), "FEE_NET", "fee"),
    ]
    ranked = prioritize(rows)
    assert ranked[0]["taxonomy_code"] == "UNRESOLVED"
    assert ranked[0]["decision"] == "ESCALATE"
    assert ranked[0]["severity"] == "critical"
    assert decision_for_taxonomy("FEE_NET") == "HOLD"
    assert decorate_exception(rows[2])["financial_impact_inr"] == 12000.0


def test_report_states_precision_scope_and_n(tmp_path: Path):
    mem = ExceptionMemory(tmp_path / "mem.json")
    result = run_pipeline(
        DATA / "source_a.csv",
        DATA / "source_b.csv",
        audit_path=tmp_path / "audit.jsonl",
        memory=mem,
        enable_llm=False,
    )
    report = build_report(result, batch=1)
    assert result.source_a_count == 88
    assert "/" in report["match_rate_a_label"]
    assert report["matched_a"] == 71
    assert "71/88" in report["match_rate_a_label"] or f"{report['matched_a']}/88" in report["match_rate_a_label"]
    scoring = report["offline_scoring"]
    assert scoring["false"] == 0
    assert f"{scoring['true']}/{scoring['pairs']}" in report["precision_label"]
    assert PRECISION_NOTE.split("—")[0].strip() in report["precision_note"]
    assert report["financial"]["exposure_inr"] > 0
    assert report["wall_clock_seconds"] >= 0
    assert report["scale"]["per_1000_records"]["llm_usd_estimate"] >= 0
    assert report["batch_kind"] == "full_close"


def test_batch2_is_full_size_close():
    import pandas as pd

    a2 = pd.read_csv(DATA / "batch2_source_a.csv")
    a1 = pd.read_csv(DATA / "source_a.csv")
    assert len(a2) == len(a1) == 88


def test_eval_easy_is_mostly_rules(tmp_path: Path):
    mem = ExceptionMemory(tmp_path / "mem.json")
    result = run_pipeline(
        DATA / "eval" / "easy_a.csv",
        DATA / "eval" / "easy_b.csv",
        audit_path=tmp_path / "audit.jsonl",
        memory=mem,
        enable_llm=False,
    )
    report = build_report(result, batch=10)
    assert result.match_rate_a == 1.0
    assert result.a_records_by_stage().get("rule", 0) == result.source_a_count
    assert report["offline_scoring"]["precision"] == 1.0
