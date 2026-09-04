"""Shared result types for the matching pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

TAXONOMY_CODES = (
    "DUP",
    "SPLIT",
    "FX_ROUND",
    "FEE_NET",
    "TIME_LAG",
    "PARTIAL",
    "OOP",
    "UNRESOLVED",
)

LLM_RESOLVABLE = {"DUP", "SPLIT", "FX_ROUND", "FEE_NET", "TIME_LAG", "PARTIAL", "OOP"}
# These stay on the exception list for operator Accept → Exception Memory.
# LLM may not silent-match them even above the confidence floor.
MEMORY_POLICY_CODES = frozenset({"FEE_NET", "TIME_LAG"})


@dataclass
class Match:
    txn_id: str
    ledger_id: str
    stage: str  # rule | fuzzy | learned_rule | classifier | llm_assisted
    confidence: float
    reason: str
    amount_delta: Decimal
    date_delta_days: int
    description_similarity: float | None = None
    order_ref: str = ""
    taxonomy_code: str | None = None
    provider: str | None = None
    txn_ids: list[str] = field(default_factory=list)
    ledger_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.txn_ids:
            self.txn_ids = [self.txn_id]
        if not self.ledger_ids:
            self.ledger_ids = [self.ledger_id]


@dataclass
class ExceptionRecord:
    exception_id: str
    source: str  # A | B
    record_id: str
    order_ref: str
    amount: Decimal
    taxonomy_code: str
    reason: str
    counterpart_ids: list[str] = field(default_factory=list)
    confidence: float | None = None
    llm_reason: str | None = None
    description: str = ""
    currency: str = "INR"

    def to_dict(self) -> dict:
        return {
            "exception_id": self.exception_id,
            "source": self.source,
            "record_id": self.record_id,
            "order_ref": self.order_ref,
            "amount": str(self.amount),
            "taxonomy_code": self.taxonomy_code,
            "reason": self.reason,
            "counterpart_ids": self.counterpart_ids,
            "confidence": self.confidence,
            "llm_reason": self.llm_reason,
            "description": self.description,
            "currency": self.currency,
        }


@dataclass
class UnmatchedRecord:
    """Kept for backward compatibility with Phase 2 call sites."""

    source: str
    record_id: str
    order_ref: str
    amount: Decimal
    reason: str
    taxonomy_code: str = ""


@dataclass
class MatchResult:
    matches: list[Match] = field(default_factory=list)
    exceptions: list[ExceptionRecord] = field(default_factory=list)
    unmatched_a: list[UnmatchedRecord] = field(default_factory=list)
    unmatched_b: list[UnmatchedRecord] = field(default_factory=list)
    source_a_count: int = 0
    source_b_count: int = 0
    llm_skipped_reason: str | None = None
    llm_provider: str | None = None
    stage_seconds: dict[str, float] = field(default_factory=dict)
    wall_clock_seconds: float = 0.0
    amount_total_a: Decimal = Decimal("0.00")
    amount_matched_a: Decimal = Decimal("0.00")
    amount_exception_a: Decimal = Decimal("0.00")
    audit_events: list[dict] = field(default_factory=list)
    agent_trace: list[dict] = field(default_factory=list)
    agent_proposals: list[dict] = field(default_factory=list)
    agent_charter: str = ""

    def matched_txn_ids(self) -> set[str]:
        ids: set[str] = set()
        for match in self.matches:
            ids.update(match.txn_ids)
        return ids

    def matched_ledger_ids(self) -> set[str]:
        ids: set[str] = set()
        for match in self.matches:
            ids.update(match.ledger_ids)
        return ids

    @property
    def match_rate_a(self) -> float:
        if self.source_a_count == 0:
            return 0.0
        return len(self.matched_txn_ids()) / self.source_a_count

    @property
    def match_rate_b(self) -> float:
        if self.source_b_count == 0:
            return 0.0
        return len(self.matched_ledger_ids()) / self.source_b_count

    def by_stage(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in self.matches:
            counts[match.stage] = counts.get(match.stage, 0) + 1
        return counts

    def a_records_by_stage(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in self.matches:
            counts[match.stage] = counts.get(match.stage, 0) + len(match.txn_ids)
        return counts
