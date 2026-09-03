"""End-to-end batch 1 / learned-rule batch 2."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from src.exception_memory import ExceptionMemory
from src.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def test_batch1_classifies_every_residue(tmp_path: Path):
    mem = ExceptionMemory(tmp_path / "mem.json")
    result = run_pipeline(
        DATA / "source_a.csv",
        DATA / "source_b.csv",
        audit_path=tmp_path / "audit.jsonl",
        memory=mem,
        enable_llm=False,
    )
    assert result.source_a_count == 88
    assert result.match_rate_a > 0.75
    codes = {e.taxonomy_code for e in result.exceptions}
    for required in ("DUP", "FEE_NET", "TIME_LAG", "PARTIAL", "OOP", "UNRESOLVED"):
        assert required in codes
    assert all(e.taxonomy_code and e.reason.strip() for e in result.exceptions)
    split = [m for m in result.matches if m.taxonomy_code == "SPLIT"]
    assert split
    assert all(m.confidence >= 0.70 for m in result.matches if m.stage == "fuzzy")
    assert all(m.confidence == 1.0 for m in result.matches if m.stage == "rule")


def test_batch2_improves_after_fee_net_label(tmp_path: Path):
    mem = ExceptionMemory(tmp_path / "mem.json")
    batch1 = run_pipeline(
        DATA / "source_a.csv",
        DATA / "source_b.csv",
        audit_path=tmp_path / "audit1.jsonl",
        memory=mem,
        enable_llm=False,
    )
    fee = next(e for e in batch1.exceptions if e.taxonomy_code == "FEE_NET" and e.source == "A")
    mem.label_exception(
        fee.exception_id,
        "CloudStack SaaS settlements are net of 2% fee",
        taxonomy_code="FEE_NET",
        vendor="CloudStack SaaS",
        fee_rate=Decimal("0.02"),
    )
    before = run_pipeline(
        DATA / "batch2_source_a.csv",
        DATA / "batch2_source_b.csv",
        audit_path=tmp_path / "audit2_before.jsonl",
        memory=ExceptionMemory(tmp_path / "empty.json"),
        enable_llm=False,
    )
    after = run_pipeline(
        DATA / "batch2_source_a.csv",
        DATA / "batch2_source_b.csv",
        audit_path=tmp_path / "audit2_after.jsonl",
        memory=mem,
        enable_llm=False,
    )
    assert after.match_rate_a > before.match_rate_a
    assert after.a_records_by_stage().get("learned_rule", 0) >= 2
    assert before.a_records_by_stage().get("learned_rule", 0) == 0
