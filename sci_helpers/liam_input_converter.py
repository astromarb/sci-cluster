"""Convert wrangled sample tables into Liam's row-oriented MELTS input format."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

ZERO_EPSILON = 1e-10

LIAM_OXIDE_ROWS: tuple[str, ...] = (
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
)

LIAM_REQUIRED_MELTS_OXIDES: tuple[str, ...] = LIAM_OXIDE_ROWS[:15]

LIAM_PARAM_ROWS: tuple[str, ...] = (
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
)

DEFAULT_REQUIRED_DATASET_OXIDES: tuple[str, ...] = (
    "SiO2",
    "TiO2",
    "MgO",
    "K2O",
    "FeO",
    "Na2O",
    "CaO",
    "MnO",
    "Al2O3",
)

REQUIRED_MELTS_PARAMS: tuple[str, ...] = (
    "Model",
    "Calculation",
    "T1",
    "T2",
    "ΔT",
    "P1",
    "P2",
    "ΔP",
    "fO2 offset",
    "fO2 buffer",
    "fO2 constraint",
)

DEFAULT_OPTIONAL_MELTS_PARAMS: dict[str, Any] = {
    "Model": "rhyolite-MELTS_v1.0.x",
    "Calculation": "QF_P_Calc",
    "T unit": "C",
    "P unit": "MPa",
    "ΔH": 0.5,
    "ΔV": 0,
    "ΔS": 0,
}

OXIDE_ALIASES: dict[str, tuple[str, ...]] = {
    "F2O -1": ("F2O -1", "F2O-1"),
}

MODEL_ALIASES: dict[str, str] = {
    "1.0.x": "rhyolite-MELTS_v1.0.x",
    "1.0.2": "rhyolite-MELTS_v1.0.x",
    "1.1.x": "rhyolite-MELTS_v1.1.x",
    "1.1.0": "rhyolite-MELTS_v1.1.x",
    "1.2.x": "rhyolite-MELTS_v1.2.x",
    "1.2.0": "rhyolite-MELTS_v1.2.x",
    "5.6.x": "pMELTS_v5.6.x",
    "5.6.1": "pMELTS_v5.6.x",
    "rhyolite-MELTS_v1.0.x": "rhyolite-MELTS_v1.0.x",
    "rhyolite-MELTS_v1.1.x": "rhyolite-MELTS_v1.1.x",
    "rhyolite-MELTS_v1.2.x": "rhyolite-MELTS_v1.2.x",
    "pMELTS_v5.6.x": "pMELTS_v5.6.x",
}

CALCULATION_ALIASES: dict[str, str] = {
    "QF_P_Calc": "QF_P_Calc",
    "qf_p_calc": "QF_P_Calc",
}

SHEET_STYLE_PARAMETER_LAYOUT: tuple[tuple[int, str, str], ...] = (
    (0, "T1 (C)", "T1"),
    (1, "T2 (C)", "T2"),
    (2, "T step (C)", "ΔT"),
    (3, "T unit", "T unit"),
    (4, "P1 (MPa)", "P1"),
    (5, "P2 (MPa)", "P2"),
    (6, "P step (MPa)", "ΔP"),
    (7, "P unit", "P unit"),
    (8, "fO2 value", "fO2 offset"),
    (9, "fO2 buffer", "fO2 buffer"),
    (10, "fO2 constraint", "fO2 constraint"),
    (12, "ΔH", "ΔH"),
    (13, "ΔV", "ΔV"),
    (14, "ΔS", "ΔS"),
)


def _candidate_oxide_keys(oxide: str) -> tuple[str, ...]:
    return OXIDE_ALIASES.get(oxide, (oxide,))


def _read_wrangled_table(wrangled_csv: str | Path) -> pd.DataFrame:
    wrangled_path = Path(wrangled_csv).expanduser().resolve()
    if not wrangled_path.exists():
        raise FileNotFoundError(f"Wrangled CSV not found: {wrangled_path}")

    raw = pd.read_csv(wrangled_path)
    if raw.empty:
        raise ValueError(f"Wrangled CSV is empty: {wrangled_path}")

    sample_col = raw.columns[0]
    if raw[sample_col].isna().all():
        raise ValueError(
            f"First column is empty in wrangled CSV: {wrangled_path}. "
            "Expected sample IDs in the first column."
        )

    table = raw.rename(columns={sample_col: "sample_id"}).copy()
    table["sample_id"] = table["sample_id"].astype(str)
    table = table.set_index("sample_id")
    return table


def _coerce_fo2_constraint(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    text = str(value).strip().upper()
    if text in {"TRUE", "T", "1", "YES", "Y"}:
        return "TRUE"
    if text in {"FALSE", "F", "0", "NO", "N"}:
        return "FALSE"
    raise ValueError(
        "Invalid fO2 constraint value. Use TRUE/FALSE (or boolean), "
        f"got: {value!r}"
    )


def _coerce_model(value: Any) -> str:
    if value is None or pd.isna(value):
        raise ValueError("Model cannot be empty.")
    key = str(value).strip()
    if key in MODEL_ALIASES:
        return MODEL_ALIASES[key]
    raise ValueError(
        "Invalid Model value. Supported examples: "
        "'rhyolite-MELTS_v1.0.x', 'rhyolite-MELTS_v1.1.x', "
        "'rhyolite-MELTS_v1.2.x', 'pMELTS_v5.6.x'. "
        f"Got: {value!r}"
    )


def _coerce_calculation(value: Any) -> str:
    if value is None or pd.isna(value):
        raise ValueError("Calculation cannot be empty.")
    key = str(value).strip()
    if key in CALCULATION_ALIASES:
        return CALCULATION_ALIASES[key]
    raise ValueError("Only QF_P_Calc is currently supported.")


def _coerce_temperature_unit(value: Any) -> str:
    if value is None or pd.isna(value):
        return "C"
    text = str(value).strip().upper().replace("°", "")
    if text in {"C", "CELSIUS"}:
        return "C"
    raise ValueError(f"Invalid T unit. Use C. Got: {value!r}")


def _coerce_pressure_unit(value: Any) -> str:
    if value is None or pd.isna(value):
        return "MPa"
    text = str(value).strip().upper()
    if text == "MPA":
        return "MPa"
    raise ValueError(f"Invalid P unit. Use MPa. Got: {value!r}")


def _build_melts_param_rows(melts_params: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_OPTIONAL_MELTS_PARAMS)
    merged.update(dict(melts_params))

    missing = [key for key in REQUIRED_MELTS_PARAMS if key not in merged]
    if missing:
        missing_str = ", ".join(missing)
        raise ValueError(f"Missing required MELTS params: {missing_str}")

    merged["Model"] = _coerce_model(merged["Model"])
    merged["Calculation"] = _coerce_calculation(merged["Calculation"])
    merged["T unit"] = _coerce_temperature_unit(merged.get("T unit"))
    merged["P unit"] = _coerce_pressure_unit(merged.get("P unit"))
    merged["fO2 constraint"] = _coerce_fo2_constraint(merged["fO2 constraint"])
    return {row: merged[row] for row in LIAM_PARAM_ROWS}


def _normalize_melts_value(value: Any) -> Any:
    """Keep missing values empty; map explicit numeric zeros to a small epsilon."""
    if value is None or pd.isna(value):
        return pd.NA
    if isinstance(value, bool):
        return value
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    return ZERO_EPSILON if numeric == 0.0 else numeric


def build_liam_input_for_sample(
    wrangled_csv: str | Path,
    sample_id: str,
    melts_params: Mapping[str, Any],
    fixed_oxide_overrides: Mapping[str, float] | None = None,
    required_dataset_oxides: tuple[str, ...] | list[str] = DEFAULT_REQUIRED_DATASET_OXIDES,
) -> pd.DataFrame:
    """
    Build a single-column Liam/MELTS input table for one sample.

    Output schema:
    - index: Liam row labels (oxides first, then MELTS params)
    - one data column named with sample_id
    """
    table = _read_wrangled_table(wrangled_csv)
    if sample_id not in table.index:
        sample_preview = ", ".join(table.index[:8].tolist())
        raise ValueError(
            f"Sample '{sample_id}' not found in {wrangled_csv}. "
            f"First sample IDs: {sample_preview}"
        )

    sample_row = table.loc[sample_id]
    if isinstance(sample_row, pd.DataFrame):
        sample_row = sample_row.iloc[0]
    sample_row = sample_row.copy()

    overrides = dict(fixed_oxide_overrides or {})
    required = tuple(required_dataset_oxides)
    missing_required: list[str] = []
    for oxide in required:
        if oxide in overrides:
            continue
        if oxide not in sample_row.index or pd.isna(sample_row[oxide]):
            missing_required.append(oxide)
    if missing_required:
        missing_str = ", ".join(missing_required)
        raise ValueError(
            f"Sample '{sample_id}' is missing required oxides: {missing_str}. "
            "Provide them in wrangled data or fixed_oxide_overrides."
        )

    oxide_rows: dict[str, Any] = {}
    for oxide in LIAM_OXIDE_ROWS:
        value = None
        for key in _candidate_oxide_keys(oxide):
            if key in overrides:
                value = float(overrides[key])
                break
        if value is None:
            for key in _candidate_oxide_keys(oxide):
                if key in sample_row.index and not pd.isna(sample_row[key]):
                    value = float(sample_row[key])
                    break
        if value is None and oxide in LIAM_REQUIRED_MELTS_OXIDES:
            # Liam's MELTS workflow is more stable with explicit numeric values
            # for the first 15 oxide rows.
            value = 0.0
        oxide_rows[oxide] = _normalize_melts_value(value)

    param_rows_raw = _build_melts_param_rows(melts_params)
    param_rows = {key: _normalize_melts_value(value) for key, value in param_rows_raw.items()}
    row_order = list(LIAM_OXIDE_ROWS) + list(LIAM_PARAM_ROWS)
    values = {row: oxide_rows.get(row, param_rows.get(row)) for row in row_order}
    out = pd.DataFrame({sample_id: [values[row] for row in row_order]}, index=row_order)
    out.index.name = ""
    return out


def write_liam_input_csv(liam_table: pd.DataFrame, output_csv: str | Path) -> Path:
    output_path = Path(output_csv).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    liam_table.to_csv(output_path)
    return output_path


def build_sheet_style_input_view(liam_table: pd.DataFrame) -> pd.DataFrame:
    """
    Build a spreadsheet-like view:
    - Col A/B: oxide label/value
    - Col D/E: run parameter label/value
    - Col G/H: model/calculation label/value
    """
    if liam_table.shape[1] != 1:
        raise ValueError("Sheet-style view expects a single composition column.")
    sample_col = str(liam_table.columns[0])

    n_rows = max(len(LIAM_OXIDE_ROWS), 15)
    out = pd.DataFrame("", index=range(n_rows), columns=range(8))

    for i, oxide in enumerate(LIAM_OXIDE_ROWS):
        out.iat[i, 0] = oxide
        out.iat[i, 1] = liam_table.at[oxide, sample_col] if oxide in liam_table.index else ""

    for row_idx, display_label, param_key in SHEET_STYLE_PARAMETER_LAYOUT:
        out.iat[row_idx, 3] = display_label
        out.iat[row_idx, 4] = liam_table.at[param_key, sample_col] if param_key in liam_table.index else ""

    out.iat[0, 6] = "Model"
    out.iat[0, 7] = liam_table.at["Model", sample_col] if "Model" in liam_table.index else ""
    out.iat[1, 6] = "Calculation"
    out.iat[1, 7] = liam_table.at["Calculation", sample_col] if "Calculation" in liam_table.index else ""

    return out


def write_sheet_style_input_csv(liam_table: pd.DataFrame, output_csv: str | Path) -> Path:
    output_path = Path(output_csv).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_df = build_sheet_style_input_view(liam_table)
    sheet_df.to_csv(output_path, index=False, header=False)
    return output_path


def build_and_write_liam_input_for_sample(
    wrangled_csv: str | Path,
    sample_id: str,
    melts_params: Mapping[str, Any],
    output_csv: str | Path,
    fixed_oxide_overrides: Mapping[str, float] | None = None,
    required_dataset_oxides: tuple[str, ...] | list[str] = DEFAULT_REQUIRED_DATASET_OXIDES,
) -> Path:
    table = build_liam_input_for_sample(
        wrangled_csv=wrangled_csv,
        sample_id=sample_id,
        melts_params=melts_params,
        fixed_oxide_overrides=fixed_oxide_overrides,
        required_dataset_oxides=required_dataset_oxides,
    )
    return write_liam_input_csv(table, output_csv=output_csv)
