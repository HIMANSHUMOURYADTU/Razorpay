"""Persisted store of human-labeled exception patterns (the compounding-accuracy piece)."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.match_utils import extract_vendor

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE = REPO_ROOT / "data" / "exception_memory.json"
LAST_EXCEPTIONS = REPO_ROOT / "data" / "last_exceptions.json"


@dataclass
class LearnedPattern:
    pattern_id: str
    taxonomy_code: str
    rule: str
    times_applied: int = 0
    vendor: str | None = None
    fee_rate: str | None = None  # Decimal as string for JSON stability
    date_window_days: int | None = None
    ignore_period_guard: bool = False
    source_exception_ids: list[str] | None = None
    created_at: str = ""

    def fee_rate_decimal(self) -> Decimal | None:
        if self.fee_rate is None or self.fee_rate == "":
            return None
        return Decimal(self.fee_rate)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(patterns: list[dict[str, Any]]) -> str:
    n = len(patterns) + 1
    return f"pat_{n:03d}"


class ExceptionMemory:
    def __init__(self, path: str | Path = DEFAULT_STORE) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"patterns": []})

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"patterns": []}
        # utf-8-sig strips a Windows BOM if PowerShell Set-Content wrote the file.
        return json.loads(self.path.read_text(encoding="utf-8-sig"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_patterns(self) -> list[LearnedPattern]:
        rows = self._read().get("patterns", [])
        return [LearnedPattern(**{k: r[k] for k in LearnedPattern.__dataclass_fields__ if k in r}) for r in rows]

    def save_exceptions(self, exceptions: list[dict[str, Any]], path: Path = LAST_EXCEPTIONS) -> None:
        path.write_text(json.dumps(exceptions, indent=2, default=str), encoding="utf-8")

    def load_exceptions(self, path: Path = LAST_EXCEPTIONS) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def increment(self, pattern_id: str) -> None:
        payload = self._read()
        for row in payload.get("patterns", []):
            if row.get("pattern_id") == pattern_id:
                row["times_applied"] = int(row.get("times_applied") or 0) + 1
                break
        self._write(payload)

    def label_exception(
        self,
        exception_id: str,
        resolution_rule: str,
        *,
        taxonomy_code: str | None = None,
        vendor: str | None = None,
        fee_rate: Decimal | str | None = None,
        date_window_days: int | None = None,
        ignore_period_guard: bool = False,
    ) -> LearnedPattern:
        """Turn a human-resolved exception into a stored rule applied on later batches."""
        if not resolution_rule or not resolution_rule.strip():
            raise ValueError("resolution_rule must be a non-empty string")

        exceptions = {row["exception_id"]: row for row in self.load_exceptions()}
        source = exceptions.get(exception_id, {})
        taxonomy = (taxonomy_code or source.get("taxonomy_code") or "").upper()
        if not taxonomy:
            raise ValueError(
                f"Cannot label {exception_id}: taxonomy_code is required "
                "(pass it explicitly, or run the pipeline first so last_exceptions.json exists)."
            )

        parsed = parse_resolution_rule(resolution_rule)
        if vendor is None:
            if taxonomy == "FEE_NET":
                vendor = parsed.get("vendor") or extract_vendor(source.get("description") or "")
                if vendor == (source.get("description") or ""):
                    vendor = parsed.get("vendor")
            else:
                vendor = parsed.get("vendor")
        fee = fee_rate if fee_rate is not None else parsed.get("fee_rate")
        window = date_window_days if date_window_days is not None else parsed.get("date_window_days")
        ignore_period = ignore_period_guard or bool(parsed.get("ignore_period_guard"))

        payload = self._read()
        # Dedup: same taxonomy + vendor + fee/window already stored → just remember the exception id.
        for row in payload.get("patterns", []):
            same = (
                row.get("taxonomy_code") == taxonomy
                and (row.get("vendor") or "") == (vendor or "")
                and (row.get("fee_rate") or None) == (str(fee) if fee is not None else None)
                and row.get("date_window_days") == window
            )
            if same:
                ids = list(row.get("source_exception_ids") or [])
                if exception_id not in ids:
                    ids.append(exception_id)
                    row["source_exception_ids"] = ids
                self._write(payload)
                return LearnedPattern(**{k: row[k] for k in LearnedPattern.__dataclass_fields__ if k in row})

        pattern = LearnedPattern(
            pattern_id=_next_id(payload.get("patterns", [])),
            taxonomy_code=taxonomy,
            rule=resolution_rule.strip(),
            times_applied=0,
            vendor=vendor or None,
            fee_rate=str(fee) if fee is not None else None,
            date_window_days=window,
            ignore_period_guard=ignore_period,
            source_exception_ids=[exception_id],
            created_at=_now(),
        )
        payload.setdefault("patterns", []).append(asdict(pattern))
        self._write(payload)
        return pattern


def parse_resolution_rule(rule: str) -> dict[str, Any]:
    """Best-effort parse of a human sentence into structured fields."""
    text = rule.strip()
    out: dict[str, Any] = {}
    fee = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if fee:
        out["fee_rate"] = (Decimal(fee.group(1)) / Decimal("100")).quantize(Decimal("0.0001"))
    window = re.search(r"(\d+)\s*-?\s*day", text, re.I)
    if not window:
        window = re.search(r"T\+(\d+)", text, re.I)
    if window:
        out["date_window_days"] = int(window.group(1))
    vendor = re.search(
        r"^['\"]?([A-Za-z][A-Za-z0-9 &.'+-]+?)\s+settlements\s+are\b",
        text,
        re.I,
    )
    if vendor:
        out["vendor"] = vendor.group(1).strip()
    if re.search(r"adjacent[- ]month|out[- ]of[- ]period|ignore period", text, re.I):
        out["ignore_period_guard"] = True
    return out


def label_exception(exception_id: str, resolution_rule: str, **kwargs: Any) -> LearnedPattern:
    return ExceptionMemory().label_exception(exception_id, resolution_rule, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exception memory CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    lab = sub.add_parser("label", help="Store a learned rule from an exception id")
    lab.add_argument("exception_id")
    lab.add_argument("resolution_rule")
    lab.add_argument("--taxonomy", default=None)
    lab.add_argument("--vendor", default=None)
    lab.add_argument("--fee-rate", default=None)
    lab.add_argument("--date-window-days", type=int, default=None)
    sub.add_parser("list", help="Print stored patterns")
    args = parser.parse_args()
    mem = ExceptionMemory()
    if args.cmd == "list":
        for p in mem.list_patterns():
            print(f"{p.pattern_id}  {p.taxonomy_code}  applied={p.times_applied}  {p.rule}")
        return
    pattern = mem.label_exception(
        args.exception_id,
        args.resolution_rule,
        taxonomy_code=args.taxonomy,
        vendor=args.vendor,
        fee_rate=Decimal(args.fee_rate) if args.fee_rate else None,
        date_window_days=args.date_window_days,
    )
    print(f"stored {pattern.pattern_id}: {pattern.rule}")


if __name__ == "__main__":
    main()
