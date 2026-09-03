from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from src.audit_trail import AuditTrail
from src.llm_providers.base import LLMProvider
from src.match_utils import money


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, is_match: bool, confidence: float, reason: str = "scripted") -> None:
        self.is_match = is_match
        self.confidence = confidence
        self.reason = reason
        self.calls: list[tuple[dict, dict, str]] = []

    def classify_match(self, record_a: dict, record_b: dict, taxonomy_code: str = "") -> dict:
        self.calls.append((record_a, record_b, taxonomy_code))
        return {
            "is_match": self.is_match,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def frame_a(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["order_ref_norm"] = df["order_ref"].str.casefold()
    df["settlement_date"] = pd.to_datetime(df["settlement_date"])
    df["amount"] = pd.to_numeric(df["amount"]).round(2)
    df["currency"] = df["currency"].astype(str).str.upper()
    df["description"] = df["description"].astype(str)
    return df


def frame_b(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["order_ref_norm"] = df["order_ref"].str.casefold()
    df["posting_date"] = pd.to_datetime(df["posting_date"])
    df["amount"] = pd.to_numeric(df["amount"]).round(2)
    df["currency"] = df["currency"].astype(str).str.upper()
    df["description"] = df["description"].astype(str)
    return df


def row_a(txn_id, order_ref, amount, date, desc="Razorpay settlement | Acme | x", currency="INR"):
    return {
        "txn_id": txn_id,
        "order_ref": order_ref,
        "amount": amount,
        "settlement_date": date,
        "description": desc,
        "currency": currency,
    }


def row_b(ledger_id, order_ref, amount, date, desc="Ledger posting - Acme | x", currency="INR"):
    return {
        "ledger_id": ledger_id,
        "order_ref": order_ref,
        "amount": amount,
        "posting_date": date,
        "description": desc,
        "currency": currency,
    }


@pytest.fixture
def audit(tmp_path: Path) -> AuditTrail:
    return AuditTrail(tmp_path / "audit.jsonl")
