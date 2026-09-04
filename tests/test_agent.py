"""Agent loop uses the same matchers; operator must accept policy."""

from pathlib import Path

from src.agent.operator import ProposalQueue
from src.agent.planner import plan_next
from src.exception_memory import ExceptionMemory
from src.pipeline import run_pipeline
from reports.report_generator import build_report

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def test_planner_never_skips_rule_floor():
    first = plan_next({"ran": [], "enable_llm": False, "unmatched_a": 88, "memory_n": 0})
    assert first["tool"] == "rule_match"
    second = plan_next({"ran": {"rule_match"}, "enable_llm": False, "unmatched_a": 27, "memory_n": 0})
    assert second["tool"] == "apply_memory"


def test_agent_close_matches_old_rates(tmp_path: Path):
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
    tools = [s["tool"] for s in result.agent_trace]
    assert tools[0] == "rule_match"
    assert "classify_exceptions" in tools
    assert "propose_policies" in tools
    assert tools[-1] == "stop"
    assert result.agent_proposals
    assert all(p.get("requires_human") for p in result.agent_proposals)
    first = result.agent_proposals[0]
    assert first.get("taxonomy_code") == "FEE_NET"
    assert "CloudStack" in (first.get("vendor") or first.get("proposed_rule") or "")
    report = build_report(result, batch=1)
    assert report["offline_scoring"]["false"] == 0
    assert report["matched_a"] == 71


def test_operator_accept_writes_memory_reject_does_not(tmp_path: Path):
    mem = ExceptionMemory(tmp_path / "mem.json")
    result = run_pipeline(
        DATA / "source_a.csv",
        DATA / "source_b.csv",
        audit_path=tmp_path / "audit.jsonl",
        memory=mem,
        enable_llm=False,
    )
    queue = ProposalQueue(tmp_path / "agent_proposals.json")
    fee = next(p for p in result.agent_proposals if p.get("taxonomy_code") == "FEE_NET" and p.get("executable"))
    queue.replace_pending(result.agent_proposals)
    before = len(mem.list_patterns())
    queue.accept(fee["proposal_id"], mem)
    assert len(mem.list_patterns()) == before + 1
    lag = next(p for p in queue.pending() if p.get("taxonomy_code") == "TIME_LAG")
    queue.reject(lag["proposal_id"], "not this cycle")
    assert len(mem.list_patterns()) == before + 1
    assert next(p for p in queue.list_all() if p["proposal_id"] == lag["proposal_id"])["status"] == "rejected"
