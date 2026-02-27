"""Utilities for extracting MELTS workbook data into analysis tables."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import pandas as pd

_CALL_ID_COLUMNS = ["sample_id", "phase_name", "Index", "T (C)", "P (MPa)"]
_CALL_REQUIRED_SHEET_COLUMNS = {"Index", "T (C)", "P (MPa)"}


def _infer_sample_id_from_workbook(workbook_path: Path) -> str:
    stem = workbook_path.stem
    match = re.search(r"Parallel_MELTS_(.+?)_dP", stem)
    if match:
        return match.group(1)
    return stem


def _safe_sheet_name(sheet_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", sheet_name.strip())


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        converted = pd.to_numeric(out[col], errors="coerce")
        if converted.notna().any():
            out[col] = converted
    return out


def extract_workbook_tables(workbook_path: str | Path) -> dict[str, pd.DataFrame]:
    """Return all workbook sheets as DataFrames keyed by sheet name."""
    workbook = Path(workbook_path).expanduser().resolve()
    if not workbook.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook}")
    tables = pd.read_excel(workbook, sheet_name=None, engine="openpyxl")
    return {sheet_name: table for sheet_name, table in tables.items()}


def write_workbook_tables(workbook_path: str | Path, output_dir: str | Path) -> list[Path]:
    """Write each workbook sheet to CSV and return written file paths."""
    workbook = Path(workbook_path).expanduser().resolve()
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tables = extract_workbook_tables(workbook)
    written: list[Path] = []
    for sheet_name, table in tables.items():
        csv_path = out_dir / f"{workbook.stem}__{_safe_sheet_name(sheet_name)}.csv"
        table.to_csv(csv_path, index=False)
        written.append(csv_path)
    return written


def extract_calls_wide(workbook_path: str | Path) -> pd.DataFrame:
    """
    Build one row per MELTS call state from thermodynamic sheets.

    Rows include at minimum:
    (sample_id, phase_name, Index, T (C), P (MPa))
    plus all numeric columns available in each sheet.
    """
    workbook = Path(workbook_path).expanduser().resolve()
    tables = extract_workbook_tables(workbook)
    sample_id = _infer_sample_id_from_workbook(workbook)

    frames: list[pd.DataFrame] = []
    for sheet_name, table in tables.items():
        if table.empty:
            continue
        table = table.dropna(how="all")
        if table.empty or not _CALL_REQUIRED_SHEET_COLUMNS.issubset(set(table.columns)):
            continue

        cur = _coerce_numeric_columns(table)
        cur["sample_id"] = sample_id
        cur["phase_name"] = sheet_name

        ordered_cols = [col for col in _CALL_ID_COLUMNS if col in cur.columns]
        remaining_cols = [col for col in cur.columns if col not in ordered_cols]
        frames.append(cur[ordered_cols + remaining_cols])

    if not frames:
        return pd.DataFrame(columns=_CALL_ID_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def extract_calls_long(workbook_path: str | Path) -> pd.DataFrame:
    """
    Build long-format table with one row per property value:
    (sample_id, phase_name, Index, T (C), P (MPa), property_name, property_value)
    """
    wide_df = extract_calls_wide(workbook_path)
    if wide_df.empty:
        return pd.DataFrame(columns=[*_CALL_ID_COLUMNS, "property_name", "property_value"])

    id_vars = [col for col in _CALL_ID_COLUMNS if col in wide_df.columns]
    value_vars = [col for col in wide_df.columns if col not in id_vars and col != "Outcome"]
    if not value_vars:
        return pd.DataFrame(columns=[*id_vars, "property_name", "property_value"])

    long_df = wide_df.melt(
        id_vars=id_vars,
        value_vars=value_vars,
        var_name="property_name",
        value_name="property_value",
    )
    long_df["property_value"] = pd.to_numeric(long_df["property_value"], errors="coerce")
    long_df = long_df.dropna(subset=["property_value"]).reset_index(drop=True)
    return long_df


__all__ = [
    "extract_workbook_tables",
    "write_workbook_tables",
    "extract_calls_wide",
    "extract_calls_long",
]

