"""Configurable matching tolerances. Nothing below these thresholds is force-matched."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class MatchConfig:
    # Stage 1 (deterministic): order_ref + amount + date window. Confidence is always 1.0.
    stage1_date_window_days: int = 3
    stage1_amount_tolerance: Decimal = Decimal("0.01")

    # Stage 2 (fuzzy): residual records only. Confidence is scaled by the band used.
    stage2_date_window_days: int = 7
    stage2_amount_bands: tuple[Decimal, ...] = field(
        default_factory=lambda: (
            Decimal("0.05"),
            Decimal("0.25"),
            Decimal("1.00"),
        )
    )
    stage2_min_description_similarity: float = 40.0
    stage2_min_confidence: float = 0.70

    # Accounting-period guard: July posting vs August settlement is OOP, not a fuzzy match.
    require_same_calendar_month: bool = True
    require_same_currency: bool = True

    # Stage 3 classifier reconstruction.
    fee_percentages: tuple[Decimal, ...] = field(
        default_factory=lambda: (Decimal("0.02"), Decimal("0.0236"))
    )
    fee_amount_tolerance: Decimal = Decimal("0.05")
    split_amount_tolerance: Decimal = Decimal("0.05")
    partial_ratio_min: Decimal = Decimal("0.50")
    partial_ratio_max: Decimal = Decimal("0.90")
    learned_rule_confidence: float = 0.95

    # Stage 4 LLM gate. Never force-match below this.
    llm_confidence_threshold: float = field(
        default_factory=lambda: _env_float("LLM_CONFIDENCE_THRESHOLD", 0.75)
    )
    enable_llm: bool = True


DEFAULT_CONFIG = MatchConfig()
