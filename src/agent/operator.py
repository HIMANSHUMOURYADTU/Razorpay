"""Human operator queue. Agent drafts; only Accept writes Exception Memory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.exception_memory import ExceptionMemory

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_QUEUE = REPO_ROOT / "data" / "agent_proposals.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProposalQueue:
    def __init__(self, path: str | Path = DEFAULT_QUEUE) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"proposals": []})

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"proposals": []}
        return json.loads(self.path.read_text(encoding="utf-8-sig"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def replace_pending(self, drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload = self._read()
        kept = [p for p in payload.get("proposals", []) if p.get("status") != "pending"]
        payload["proposals"] = kept + drafts
        self._write(payload)
        return drafts

    def list_all(self) -> list[dict[str, Any]]:
        return list(self._read().get("proposals", []))

    def pending(self) -> list[dict[str, Any]]:
        return [p for p in self.list_all() if p.get("status") == "pending"]

    def _update(self, proposal_id: str, **fields: Any) -> dict[str, Any]:
        payload = self._read()
        found = None
        for row in payload.get("proposals", []):
            if row.get("proposal_id") == proposal_id:
                row.update(fields)
                row["resolved_at"] = _now()
                found = row
                break
        if found is None:
            raise KeyError(proposal_id)
        self._write(payload)
        return found

    def reject(self, proposal_id: str, operator_note: str = "") -> dict[str, Any]:
        return self._update(proposal_id, status="rejected", operator_note=operator_note)

    def accept(
        self,
        proposal_id: str,
        memory: ExceptionMemory,
        *,
        proposed_rule: str | None = None,
        taxonomy_code: str | None = None,
        vendor: str | None = None,
        fee_rate: str | None = None,
        date_window_days: int | None = None,
        ignore_period_guard: bool | None = None,
        operator_note: str = "",
    ) -> dict[str, Any]:
        """Operator Accept (optional edits). Only then does a learned_rule exist."""
        payload = self._read()
        row = next((p for p in payload.get("proposals", []) if p.get("proposal_id") == proposal_id), None)
        if row is None:
            raise KeyError(proposal_id)
        if not row.get("executable", True):
            raise ValueError("This proposal is not an executable policy — reject or handle in ops.")
        rule = (proposed_rule if proposed_rule is not None else row.get("proposed_rule") or "").strip()
        tax = (taxonomy_code or row.get("taxonomy_code") or "").upper()
        ven = vendor if vendor is not None else row.get("vendor")
        fee = fee_rate if fee_rate is not None else row.get("fee_rate")
        window = date_window_days if date_window_days is not None else row.get("date_window_days")
        oop = row.get("ignore_period_guard") if ignore_period_guard is None else ignore_period_guard
        pattern = memory.label_exception(
            row["exception_id"],
            rule,
            taxonomy_code=tax,
            vendor=ven,
            fee_rate=Decimal(str(fee)) if fee not in (None, "") else None,
            date_window_days=int(window) if window not in (None, "") else None,
            ignore_period_guard=bool(oop),
        )
        edited = bool(proposed_rule and proposed_rule.strip() != (row.get("proposed_rule") or "").strip())
        status = "edited" if edited else "accepted"
        return self._update(
            proposal_id,
            status=status,
            operator_note=operator_note,
            proposed_rule=rule,
            pattern_id=pattern.pattern_id,
        )
