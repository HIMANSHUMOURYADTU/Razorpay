"""Structured JSONL audit trail. Every match and every exception writes one line."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditTrail:
    """Append-only JSONL logger. One object per decision, never a silent skip."""

    def __init__(self, path: str | Path, *, reset: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if reset else "a"
        self._fh = self.path.open(mode, encoding="utf-8")
        self.events: list[dict[str, Any]] = []

    def log(
        self,
        *,
        stage: str,
        reason: str,
        record_ids: dict[str, Any],
        confidence: float | None,
        taxonomy_code: str | None = None,
        decision: str = "match",
        provider: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cleaned_ids = {k: v for k, v in record_ids.items() if v is not None and v != ""}
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "record_ids": cleaned_ids,
            "stage": stage,
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
        }
        if taxonomy_code:
            event["taxonomy_code"] = taxonomy_code
        if provider:
            event["provider"] = provider
        if extra:
            event["extra"] = extra
        self._fh.write(json.dumps(event, default=str) + "\n")
        self._fh.flush()
        self.events.append(event)
        return event

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "AuditTrail":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
