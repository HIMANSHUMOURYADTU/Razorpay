"""Load and normalize settlement (source A) and ledger (source B) CSVs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Hidden scoring labels — dropped if a caller accidentally concatenates them onto a source file.
HIDDEN_COLUMNS = {"ground_truth", "gt_group", "gt_notes", "taxonomy", "expected_behavior"}

SOURCE_A_REQUIRED = [
    "txn_id",
    "order_ref",
    "amount",
    "settlement_date",
    "description",
    "currency",
]
SOURCE_B_REQUIRED = [
    "ledger_id",
    "order_ref",
    "amount",
    "posting_date",
    "description",
    "currency",
]


def _drop_hidden(df: pd.DataFrame) -> pd.DataFrame:
    present = [c for c in df.columns if c in HIDDEN_COLUMNS]
    if present:
        df = df.drop(columns=present)
    return df


def _require_columns(df: pd.DataFrame, required: list[str], path: Path) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing required columns {missing}")


def _parse_dates(series: pd.Series, column: str, path: Path) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", format="mixed", dayfirst=False)
    bad = parsed.isna() & series.notna() & (series.astype(str).str.strip() != "")
    if bad.any():
        sample = series[bad].head(3).tolist()
        raise ValueError(f"{path}: could not parse {column} values {sample}")
    return parsed


def _normalize_strings(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for col in columns:
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df


def load_source_a(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    df = pd.read_csv(path)
    df = _drop_hidden(df)
    _require_columns(df, SOURCE_A_REQUIRED, path)
    df = df[SOURCE_A_REQUIRED].copy()
    df = _normalize_strings(df, ["txn_id", "order_ref", "description", "currency"])
    df["currency"] = df["currency"].str.upper()
    df["order_ref_norm"] = df["order_ref"].str.casefold()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").round(2)
    if df["amount"].isna().any():
        raise ValueError(f"{path}: non-numeric amount values present")
    df["settlement_date"] = _parse_dates(df["settlement_date"], "settlement_date", path)
    if df["txn_id"].duplicated().any():
        dupes = df.loc[df["txn_id"].duplicated(), "txn_id"].tolist()
        raise ValueError(f"{path}: duplicate txn_id values {dupes[:5]}")
    df = df.reset_index(drop=True)
    df["_row_id"] = df.index
    return df


def load_source_b(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    df = pd.read_csv(path)
    df = _drop_hidden(df)
    _require_columns(df, SOURCE_B_REQUIRED, path)
    df = df[SOURCE_B_REQUIRED].copy()
    df = _normalize_strings(df, ["ledger_id", "order_ref", "description", "currency"])
    df["currency"] = df["currency"].str.upper()
    df["order_ref_norm"] = df["order_ref"].str.casefold()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").round(2)
    if df["amount"].isna().any():
        raise ValueError(f"{path}: non-numeric amount values present")
    df["posting_date"] = _parse_dates(df["posting_date"], "posting_date", path)
    if df["ledger_id"].duplicated().any():
        dupes = df.loc[df["ledger_id"].duplicated(), "ledger_id"].tolist()
        raise ValueError(f"{path}: duplicate ledger_id values {dupes[:5]}")
    df = df.reset_index(drop=True)
    df["_row_id"] = df.index
    return df
