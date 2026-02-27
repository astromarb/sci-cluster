"""Wrangled-to-Liam MELTS input conversion utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

DEFAULT_REQUIRED_DATASET_OXIDES = [
    "SiO2",
    "TiO2",
    "MgO",
    "K2O",
    "FeO",
    "Na2O",
    "CaO",
    "MnO",
    "Al2O3",
]

DEFAULT_REQUIRED_OUTPUT_OXIDES = [
    "SiO2",
    "TiO2",
    "Al2O3",
    "Fe2O3",
    "Cr2O3",
    "FeO",
    "MnO",
    "MgO",
    "NiO",
    "CoO",
    "CaO",
    "Na2O",
    "K2O",
    "P2O5",
    "H2O",
    "CO2",
    "SO3",
    "Cl2O-1",
    "F2O -1",
]

DEFAULT_MELTS_PARAM_ORDER = [
    "Model",
    "Calculation",
    "T1",
    "T2",
    "ΔT",
    "T unit",
    "P1",
    "P2",
    "ΔP",
    "P unit",
    "fO2 offset",
    "fO2 buffer",
    "fO2 constraint",
    "ΔH",
    "ΔV",
    "ΔS",
]

_MELTS_PARAM_ALIAS_TO_CANONICAL = {
    "model": "Model",
    "calculation": "Calculation",
    "t1": "T1",
    "t2": "T2",
    "dt": "ΔT",
    "deltat": "ΔT",
    "delta_t": "ΔT",
    "tunit": "T unit",
    "t_unit": "T unit",
    "p1": "P1",
    "p2": "P2",
    "dp": "ΔP",
    "deltap": "ΔP",
    "delta_p": "ΔP",
    "punit": "P unit",
    "p_unit": "P unit",
    "fo2offset": "fO2 offset",
    "fo2_offset": "fO2 offset",
    "fo2buffer": "fO2 buffer",
    "fo2_buffer": "fO2 buffer",
    "fo2constraint": "fO2 constraint",
    "fo2_constraint": "fO2 constraint",
    "dh": "ΔH",
    "deltah": "ΔH",
    "delta_h": "ΔH",
    "dv": "ΔV",
    "deltav": "ΔV",
    "delta_v": "ΔV",
    "ds": "ΔS",
    "deltas": "ΔS",
    "delta_s": "ΔS",
}


def _normalize_param_key(key: str) -> str:
    """Normalize MELTS parameter names to compare aliases robustly."""
    normalized = key.strip().replace("Δ", "delta").replace("δ", "delta")
    normalized = normalized.replace(" ", "").replace("_", "")
    return normalized.lower()


def _canonicalize_melts_params(melts_params: Mapping[str, Any]) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for raw_key, value in melts_params.items():
        normalized = _normalize_param_key(str(raw_key))
        alias_match = _MELTS_PARAM_ALIAS_TO_CANONICAL.get(normalized, str(raw_key))
        canonical[alias_match] = value
    missing = [key for key in DEFAULT_MELTS_PARAM_ORDER if key not in canonical]
    if missing:
        raise ValueError(f"Missing required MELTS params: {missing}")
    return canonical


def _serialize_param_value(value: Any) -> Any:
    if isinstance(value, (bool, np.bool_)):
        return "TRUE" if bool(value) else "FALSE"
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            return lowered.upper()
        return value.strip()
    return value


def _read_sample_row(wrangled_csv: str | Path, sample_id: str) -> pd.Series:
    wrangled_path = Path(wrangled_csv).expanduser().resolve()
    if not wrangled_path.exists():
        raise FileNotFoundError(f"Wrangled CSV not found: {wrangled_path}")

    wrangled_df = pd.read_csv(wrangled_path, index_col=0)
    if sample_id not in wrangled_df.index:
        example_ids = [str(idx) for idx in wrangled_df.index[:5].tolist()]
        raise ValueError(
            f"Sample '{sample_id}' not found in {wrangled_path}. "
            f"Example sample IDs: {example_ids}"
        )

    sample_row = wrangled_df.loc[sample_id]
    if isinstance(sample_row, pd.DataFrame):
        sample_row = sample_row.iloc[0]
    return sample_row


def build_liam_input_for_sample(
    wrangled_csv: str | Path,
    sample_id: str,
    melts_params: Mapping[str, Any],
    fixed_oxide_overrides: Mapping[str, Any] | None = None,
    required_dataset_oxides: Iterable[str] = DEFAULT_REQUIRED_DATASET_OXIDES,
    required_output_oxides: Iterable[str] = DEFAULT_REQUIRED_OUTPUT_OXIDES,
) -> pd.DataFrame:
    """
    Build Liam-format one-column MELTS input from one wrangled sample.

    The output row order is strict:
    1) Composition rows in `required_output_oxides` order.
    2) MELTS parameters in `DEFAULT_MELTS_PARAM_ORDER`.
    """
    sample_row = _read_sample_row(wrangled_csv=wrangled_csv, sample_id=sample_id)
    fixed_oxide_overrides = dict(fixed_oxide_overrides or {})
    required_dataset_oxides = list(required_dataset_oxides)
    required_output_oxides = list(required_output_oxides)

    missing_dataset_columns = [oxide for oxide in required_dataset_oxides if oxide not in sample_row.index]
    if missing_dataset_columns:
        raise ValueError(f"Wrangled dataset is missing required oxides: {missing_dataset_columns}")

    for oxide in required_dataset_oxides:
        value = pd.to_numeric(sample_row.get(oxide), errors="coerce")
        if pd.isna(value):
            raise ValueError(
                f"Sample '{sample_id}' has non-numeric or missing required oxide '{oxide}' in wrangled dataset."
            )

    canonical_melts_params = _canonicalize_melts_params(melts_params)

    rows: list[tuple[str, Any]] = []
    for oxide in required_output_oxides:
        if oxide in fixed_oxide_overrides:
            raw_value = fixed_oxide_overrides[oxide]
        elif oxide in sample_row.index:
            raw_value = sample_row[oxide]
        else:
            raw_value = 0.0

        numeric_value = pd.to_numeric(raw_value, errors="coerce")
        if pd.isna(numeric_value):
            raise ValueError(
                f"Output oxide '{oxide}' for sample '{sample_id}' is non-numeric after overrides: {raw_value!r}"
            )
        rows.append((oxide, float(numeric_value)))

    for param_name in DEFAULT_MELTS_PARAM_ORDER:
        rows.append((param_name, _serialize_param_value(canonical_melts_params[param_name])))

    out_df = pd.DataFrame(rows, columns=["Parameter", sample_id])
    return out_df


def write_liam_input_csv(
    wrangled_csv: str | Path,
    sample_id: str,
    output_csv: str | Path,
    melts_params: Mapping[str, Any],
    fixed_oxide_overrides: Mapping[str, Any] | None = None,
    required_dataset_oxides: Iterable[str] = DEFAULT_REQUIRED_DATASET_OXIDES,
    required_output_oxides: Iterable[str] = DEFAULT_REQUIRED_OUTPUT_OXIDES,
) -> Path:
    """Build and write one-sample Liam MELTS input CSV."""
    out_df = build_liam_input_for_sample(
        wrangled_csv=wrangled_csv,
        sample_id=sample_id,
        melts_params=melts_params,
        fixed_oxide_overrides=fixed_oxide_overrides,
        required_dataset_oxides=required_dataset_oxides,
        required_output_oxides=required_output_oxides,
    )
    output_path = Path(output_csv).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path, index=False)
    return output_path


__all__ = [
    "DEFAULT_REQUIRED_DATASET_OXIDES",
    "DEFAULT_REQUIRED_OUTPUT_OXIDES",
    "DEFAULT_MELTS_PARAM_ORDER",
    "build_liam_input_for_sample",
    "write_liam_input_csv",
]

