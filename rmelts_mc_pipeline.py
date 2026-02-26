from __future__ import annotations

import contextlib
import csv
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


M_FE2O3 = 159.688
M_FEO = 71.844

# Exact order required by MeltsHelperFunctions.parallel_melts_main_loop (curCol[:15]).
MELTS_REQUIRED_15_ROWS = [
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
]

MELTS_ADDITIONAL_ROWS = [
    "CO2",
    "SO3",
    "Cl2O-1",
    "F2O-1",
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
    "fO2 constraint",
    "fO2 buffer",
    "fO2 offset",
]

MELTS_ALL_ROWS = MELTS_REQUIRED_15_ROWS + MELTS_ADDITIONAL_ROWS
MELTS_OXIDE_ROWS = [
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
    "F2O-1",
]
PREPARED_MC_COLUMNS = ["sample_label"] + MELTS_OXIDE_ROWS
APLITE_XRF_DRY_OXIDE_ROWS = [
    "SiO2",
    "TiO2",
    "Al2O3",
    "FeO*",
    "MnO",
    "MgO",
    "CaO",
    "Na2O",
    "K2O",
    "P2O5",
]
REQUIRED_PRESENT_OR_DERIVABLE = {
    "SiO2",
    "Al2O3",
    "H2O",
}

RUN_PARAM_ROW_KEYS = [
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
    "fO2 constraint",
    "fO2 buffer",
    "fO2 offset",
]

RUN_PARAM_OVERRIDE_ALIAS = {
    "model": "Model",
    "calculation": "Calculation",
    "t1": "T1",
    "t2": "T2",
    "dt": "ΔT",
    "dT": "ΔT",
    "Δt": "ΔT",
    "t_unit": "T unit",
    "temperature_unit": "T unit",
    "p1": "P1",
    "p2": "P2",
    "dp": "ΔP",
    "dP": "ΔP",
    "Δp": "ΔP",
    "p_unit": "P unit",
    "pressure_unit": "P unit",
    "fo2_constraint": "fO2 constraint",
    "fo2 buffer": "fO2 buffer",
    "fo2_buffer": "fO2 buffer",
    "fo2path": "fO2 buffer",
    "fo2_offset": "fO2 offset",
}


@dataclass
class MELTSRunParams:
    T1: float
    T2: float
    dT: float
    P1: float
    P2: float
    dP: float
    fO2_constraint: str
    fO2_buffer: str
    fO2_offset: float
    model: str = "rhyolite-MELTS_v1.0.x"
    calculation: str = "QF_P_Calc"
    T_unit: str = "C"
    P_unit: str = "MPa"

    def to_melts_row_map(self) -> dict[str, Any]:
        return {
            "Model": self.model,
            "Calculation": self.calculation,
            "T1": float(self.T1),
            "T2": float(self.T2),
            "ΔT": float(self.dT),
            "T unit": self.T_unit,
            "P1": float(self.P1),
            "P2": float(self.P2),
            "ΔP": float(self.dP),
            "P unit": self.P_unit,
            "fO2 constraint": self.fO2_constraint,
            "fO2 buffer": self.fO2_buffer,
            "fO2 offset": float(self.fO2_offset),
        }


@dataclass
class ConversionResult:
    prepared_mc_csv_path: str
    qc_report_csv_path: Optional[str]
    num_samples: int
    sample_labels: list[str]
    warnings: list[str] = field(default_factory=list)
    schema_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApliteNormalizationResult:
    normalized_all_rowwise_csv_path: str
    normalized_target_rowwise_csv_path: Optional[str]
    num_samples: int
    sample_labels: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    melts_input_csv_path: str
    run_dir: str
    manifest_csv_path: str
    results: list[dict[str, Any]]
    summary: dict[str, Any]


@dataclass
class StagedRunResult:
    scout_run_result: RunResult
    fine_run_result: RunResult
    scout_first_appearance_table: Any
    fine_first_appearance_table: Any
    fine_window_metadata: dict[str, Any]
    scout_summary_workbook_path: str
    fine_summary_workbook_path: str


@dataclass
class GeobarometryBasisResult:
    phase_tables: dict[str, Any]
    merged_long_table: Any
    phase_availability_table: Any
    pressure_analysis_table: Any
    extraction_report: dict[str, Any]


@dataclass(frozen=True)
class _HelperPatchProfile:
    name: str
    fix_liquidus_timeout_loop: bool = False
    wrap_main_execute_with_timeout: bool = False
    skip_wet_liquidus_presearch: bool = False
    timeout_path_advances_temperature: bool = False


_HELPER_PATCH_PROFILES: dict[str, _HelperPatchProfile] = {
    "profile_a_unpatched": _HelperPatchProfile(name="profile_a_unpatched"),
    "profile_b_stable_import_only": _HelperPatchProfile(name="profile_b_stable_import_only"),
    "profile_c_liquidus_guard_only": _HelperPatchProfile(
        name="profile_c_liquidus_guard_only",
        fix_liquidus_timeout_loop=True,
    ),
    "profile_d_safe_execute_only": _HelperPatchProfile(
        name="profile_d_safe_execute_only",
        fix_liquidus_timeout_loop=True,
        wrap_main_execute_with_timeout=True,
    ),
    "profile_e_current_full_patch": _HelperPatchProfile(
        name="profile_e_current_full_patch",
        fix_liquidus_timeout_loop=True,
        wrap_main_execute_with_timeout=True,
        skip_wet_liquidus_presearch=True,
    ),
    "profile_f_corrected_timeout_progression": _HelperPatchProfile(
        name="profile_f_corrected_timeout_progression",
        fix_liquidus_timeout_loop=True,
        wrap_main_execute_with_timeout=True,
        skip_wet_liquidus_presearch=True,
        timeout_path_advances_temperature=True,
    ),
}

_DEFAULT_HELPER_PATCH_PROFILE_NAME = "profile_e_current_full_patch"


class RMeltsPipelineError(RuntimeError):
    """Base error for this module."""


def _require_dependency(module_name: str) -> Any:
    try:
        return __import__(module_name)
    except ImportError as exc:
        raise RMeltsPipelineError(
            f"Required dependency '{module_name}' is not installed in this Python environment."
        ) from exc


def _require_pandas() -> Any:
    return _require_dependency("pandas")


def _require_openpyxl() -> Any:
    return _require_dependency("openpyxl")


def _now_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _resolve_helper_patch_profile(profile: Optional[Any] = None) -> _HelperPatchProfile:
    if profile is None:
        return _HELPER_PATCH_PROFILES[_DEFAULT_HELPER_PATCH_PROFILE_NAME]
    if isinstance(profile, _HelperPatchProfile):
        return profile
    key = str(profile)
    if key in _HELPER_PATCH_PROFILES:
        return _HELPER_PATCH_PROFILES[key]
    raise RMeltsPipelineError(f"Unknown helper patch profile: {profile}")


def _pressure_analysis_row_defaults() -> dict[str, Any]:
    return {
        "pressure_calc_method": None,
        "pressure_calc_error": None,
        "P_2phase_qtz_fsp_MPa": None,
        "Rmin_2phase_qtz_fsp_C": None,
        "fit_a_2phase": None,
        "fit_b_2phase": None,
        "fit_c_2phase": None,
        "P_3phase_qtz_fsp_fsp1_MPa": None,
        "Rmin_3phase_qtz_fsp_fsp1_C": None,
        "fit_a_3phase": None,
        "fit_b_3phase": None,
        "fit_c_3phase": None,
        "phase_sheet_quartz_present": None,
        "phase_sheet_feldspar_present": None,
        "phase_sheet_feldspar_1_present": None,
    }


def _runtime_row_defaults() -> dict[str, Any]:
    return {
        "melts_calc_time_s": None,
        "pressure_calc_time_s": None,
        "workbook_build_time_s": None,
        "runtime_log_version": None,
    }


def _sanitize_dataset_name(dataset_name: str) -> str:
    name = dataset_name.strip()
    if not name:
        raise ValueError("dataset_name must be non-empty")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _coerce_numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _normalize_alias_map(oxide_alias_map: Optional[dict[str, str]]) -> dict[str, str]:
    alias_map = dict(oxide_alias_map or {})
    normalized: dict[str, str] = {}
    for k, v in alias_map.items():
        normalized[k] = v
        normalized[k.lower()] = v
    return normalized


def _resolve_column_name(columns: Iterable[str], target: str, alias_map: dict[str, str]) -> Optional[str]:
    column_list = list(columns)
    if target in column_list:
        return target
    lower_lookup = {c.lower(): c for c in column_list}
    if target.lower() in lower_lookup:
        return lower_lookup[target.lower()]

    for alias_key, canonical in alias_map.items():
        if canonical != target:
            continue
        if alias_key in column_list:
            return alias_key
        if alias_key.lower() in lower_lookup:
            return lower_lookup[alias_key.lower()]
    return None


def _make_unique_labels(labels: list[str]) -> tuple[list[str], list[str]]:
    seen: dict[str, int] = {}
    out: list[str] = []
    warnings: list[str] = []
    for raw in labels:
        base = str(raw).strip() or "sample"
        count = seen.get(base, 0) + 1
        seen[base] = count
        if count == 1:
            out.append(base)
        else:
            new_label = f"{base}__{count}"
            out.append(new_label)
            warnings.append(f"Duplicate sample label '{base}' renamed to '{new_label}'")
    return out, warnings


def _fe_split_from_feot_as_feo(feot_wt: float, fe3fet: float) -> tuple[float, float]:
    feo_wt = feot_wt * (1.0 - fe3fet)
    fe2o3_wt = feot_wt * fe3fet * (M_FE2O3 / (2.0 * M_FEO))
    return feo_wt, fe2o3_wt


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:
        # numpy scalars
        return value.item()  # type: ignore[attr-defined]
    except Exception:
        return str(value)


@contextlib.contextmanager
def _pushd(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def _validate_run_params(params: MELTSRunParams) -> None:
    if params.T1 <= params.T2:
        raise ValueError("Require T1 > T2 for descending temperature runs")
    if params.dT <= 0:
        raise ValueError("Require dT > 0")
    if params.P1 <= params.P2:
        raise ValueError("Require P1 > P2 for descending pressure runs")
    if params.dP <= 0:
        raise ValueError("Require dP > 0")


def _default_sample_labels(n: int, label_prefix: str) -> list[str]:
    return [f"{label_prefix}_{i:06d}" for i in range(1, n + 1)]


def _normalize_prepared_dataframe_columns(df: Any) -> Any:
    pd = _require_pandas()
    missing = [c for c in PREPARED_MC_COLUMNS if c not in df.columns]
    if missing:
        raise RMeltsPipelineError(
            f"Prepared MC CSV missing required columns: {missing}. "
            f"Expected at least {PREPARED_MC_COLUMNS}"
        )
    ordered = df[PREPARED_MC_COLUMNS].copy()
    for oxide in MELTS_OXIDE_ROWS:
        ordered[oxide] = pd.to_numeric(ordered[oxide], errors="coerce").fillna(0.0)
    ordered["sample_label"] = ordered["sample_label"].astype(str)
    return ordered


def MC_to_csv_rMELTS(
    mc_csv_path,
    *,
    output_dir,
    dataset_name,
    sample_id_col=None,
    fe_total_col="FeOt",
    fe_total_basis="FeOt_as_FeO",
    fe3fet=0.0,
    h2o_col="H2O",
    validate_only=True,
    total_tolerance_wt=0.5,
    oxide_alias_map=None,
    label_prefix="MC",
    write_qc_report=True,
):
    """
    Prepare a row-wise Monte Carlo CSV into a standardized composition CSV for rhyolite-MELTS input assembly.

    Stage 1 assumptions:
    - Input is row-wise (one sample per row).
    - Data are already normalized; this function validates totals and does not renormalize unless validate_only=False.
    - Total iron is commonly supplied as FeOt (wt% as FeO-equivalent) and split using a batch-wide fe3fet.
    """
    pd = _require_pandas()

    if not (0.0 <= float(fe3fet) <= 1.0):
        raise ValueError("fe3fet must be between 0 and 1 inclusive")
    if fe_total_basis != "FeOt_as_FeO":
        raise NotImplementedError(
            f"Unsupported fe_total_basis={fe_total_basis!r}. Stage 1 supports 'FeOt_as_FeO'."
        )

    mc_csv_path = Path(mc_csv_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    dataset_name = _sanitize_dataset_name(str(dataset_name))
    prepared_dir = _ensure_dir(output_dir / dataset_name / "prepared")
    prepared_path = prepared_dir / "prepared_mc.csv"
    qc_path = prepared_dir / "qc_report.csv"

    df = pd.read_csv(mc_csv_path)
    if df.empty:
        raise RMeltsPipelineError(f"Monte Carlo CSV is empty: {mc_csv_path}")

    alias_map = _normalize_alias_map(oxide_alias_map)

    # Resolve sample labels
    if sample_id_col is not None and sample_id_col in df.columns:
        raw_labels = df[sample_id_col].astype(str).tolist()
    elif sample_id_col is not None:
        raise RMeltsPipelineError(
            f"sample_id_col={sample_id_col!r} not found in input columns: {list(df.columns)}"
        )
    else:
        raw_labels = _default_sample_labels(len(df), label_prefix)

    sample_labels, label_warnings = _make_unique_labels(raw_labels)
    df = df.copy()
    df["sample_label"] = sample_labels

    # Resolve key columns (including aliases if provided).
    resolved_cols: dict[str, Optional[str]] = {}
    for oxide in MELTS_OXIDE_ROWS:
        target = h2o_col if oxide == "H2O" else oxide
        resolved = _resolve_column_name(df.columns, target, alias_map)
        resolved_cols[oxide] = resolved

    fe_total_col_resolved = _resolve_column_name(df.columns, fe_total_col, alias_map)

    qc_rows: list[dict[str, Any]] = []
    prepared_rows: list[dict[str, Any]] = []
    warnings: list[str] = list(label_warnings)

    for row_index, row in df.iterrows():
        sample_label = str(row["sample_label"])
        prepared: dict[str, Any] = {"sample_label": sample_label}
        row_warnings: list[str] = []
        missing_required: list[str] = []

        # Start with direct oxides where available.
        for oxide in MELTS_OXIDE_ROWS:
            col = resolved_cols.get(oxide)
            val = _coerce_numeric(row[col]) if col is not None else None
            prepared[oxide] = 0.0 if val is None else float(val)

        # Iron handling priority:
        # 1) If both FeO and Fe2O3 columns exist directly, keep them.
        # 2) Otherwise derive from total iron column if available.
        feo_direct = resolved_cols.get("FeO") is not None
        fe2o3_direct = resolved_cols.get("Fe2O3") is not None
        feot_original = None
        if (not (feo_direct and fe2o3_direct)) and fe_total_col_resolved is not None:
            feot_original = _coerce_numeric(row[fe_total_col_resolved])
            if feot_original is not None:
                feo_wt, fe2o3_wt = _fe_split_from_feot_as_feo(float(feot_original), float(fe3fet))
                prepared["FeO"] = feo_wt
                prepared["Fe2O3"] = fe2o3_wt
            else:
                row_warnings.append(f"{fe_total_col} missing/non-numeric; FeO/Fe2O3 left from direct columns/zeros")
        elif fe_total_col_resolved is not None:
            feot_original = _coerce_numeric(row[fe_total_col_resolved])

        # Validate essential inputs (FeO/Fe2O3 allowed if both zeros, but required presence is handled above).
        for required in REQUIRED_PRESENT_OR_DERIVABLE:
            col = resolved_cols.get(required)
            if col is None and required != "H2O":
                missing_required.append(required)
            elif required == "H2O" and resolved_cols.get("H2O") is None:
                missing_required.append("H2O")

        if resolved_cols.get("FeO") is None and resolved_cols.get("Fe2O3") is None and fe_total_col_resolved is None:
            missing_required.extend(["FeO/Fe2O3_or_FeOt"])

        # Track totals
        prepared_total = float(sum(float(prepared.get(oxide, 0.0) or 0.0) for oxide in MELTS_OXIDE_ROWS))
        total_warning = abs(prepared_total - 100.0) > float(total_tolerance_wt)
        if total_warning:
            row_warnings.append(
                f"Prepared oxide total {prepared_total:.4f} wt% outside tolerance ±{total_tolerance_wt} of 100"
            )

        if not validate_only and prepared_total > 0:
            scale = 100.0 / prepared_total
            for oxide in MELTS_OXIDE_ROWS:
                prepared[oxide] = float(prepared[oxide]) * scale
            prepared_total = 100.0
            row_warnings.append("Renormalized all MELTS oxide rows to 100 wt% (validate_only=False)")

        status = "ok" if not missing_required else "error"
        if status == "error":
            warnings.append(
                f"{sample_label}: missing required inputs {missing_required}"
            )

        prepared_rows.append(prepared)
        qc_rows.append(
            {
                "sample_label": sample_label,
                "source_row_index": int(row_index),
                "status": status,
                "missing_required": ";".join(missing_required),
                "warnings": " | ".join(row_warnings),
                "fe3fet": float(fe3fet),
                "FeOt_original": feot_original,
                "FeO_prepared": prepared.get("FeO", 0.0),
                "Fe2O3_prepared": prepared.get("Fe2O3", 0.0),
                "prepared_total_wt": prepared_total,
            }
        )

    prepared_df = pd.DataFrame(prepared_rows)
    prepared_df = _normalize_prepared_dataframe_columns(prepared_df)

    # Keep only valid rows in prepared output (errors remain in QC report).
    qc_df = pd.DataFrame(qc_rows)
    valid_labels = qc_df.loc[qc_df["status"] == "ok", "sample_label"].astype(str).tolist()
    prepared_valid_df = prepared_df[prepared_df["sample_label"].isin(valid_labels)].copy()
    if prepared_valid_df.empty:
        raise RMeltsPipelineError(
            "No valid compositions were produced. Inspect the QC report logic/inputs."
        )

    prepared_valid_df.to_csv(prepared_path, index=False)
    qc_report_csv_path = None
    if write_qc_report:
        qc_df.to_csv(qc_path, index=False)
        qc_report_csv_path = str(qc_path)

    schema_summary = {
        "input_columns": [str(c) for c in df.columns],
        "prepared_columns": PREPARED_MC_COLUMNS,
        "resolved_columns": {
            k: (v if v is None else str(v)) for k, v in resolved_cols.items()
        },
        "fe_total_col_resolved": None if fe_total_col_resolved is None else str(fe_total_col_resolved),
    }

    return ConversionResult(
        prepared_mc_csv_path=str(prepared_path),
        qc_report_csv_path=qc_report_csv_path,
        num_samples=int(len(prepared_valid_df)),
        sample_labels=prepared_valid_df["sample_label"].astype(str).tolist(),
        warnings=warnings,
        schema_summary=schema_summary,
    )


def normalize_aplite_xrf_to_rowwise_mc(
    xrf_csv_path,
    *,
    output_dir,
    dataset_name,
    sample_name_col="Sample Name",
    fe_total_row="FeO*",
    fixed_h2o_wt=13.0,
    dry_normalize_to=100.0,
    target_samples=None,
):
    """
    Convert a column-wise aplite XRF table into a row-wise MC-style CSV with dry normalization and fixed H2O.

    Expected source layout (e.g., Aplites_HAL_XRF_noUncertainty.csv):
    - First column = oxide names (Sample Name)
    - Remaining columns = sample compositions
    """
    pd = _require_pandas()

    xrf_csv_path = Path(xrf_csv_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    dataset_name = _sanitize_dataset_name(str(dataset_name))
    norm_dir = _ensure_dir(output_dir / dataset_name / "normalized_inputs")

    df = pd.read_csv(xrf_csv_path)
    if df.empty:
        raise RMeltsPipelineError(f"Aplite XRF CSV is empty: {xrf_csv_path}")
    if sample_name_col not in df.columns:
        raise RMeltsPipelineError(
            f"sample_name_col={sample_name_col!r} not found in XRF columns: {list(df.columns)}"
        )

    row_key = df[sample_name_col].astype(str).str.strip()
    required_rows = list(APLITE_XRF_DRY_OXIDE_ROWS)
    if fe_total_row not in required_rows:
        # Replace default Fe row with caller-provided row name if needed.
        required_rows = [fe_total_row if r == "FeO*" else r for r in required_rows]

    work = df.copy()
    work[sample_name_col] = row_key
    work = work[work[sample_name_col].isin(required_rows)].copy()
    if work.empty:
        raise RMeltsPipelineError("No required aplite oxide rows found in XRF table")

    row_order = [r for r in required_rows if r in work[sample_name_col].tolist()]
    missing_rows = [r for r in required_rows if r not in row_order]
    if missing_rows:
        raise RMeltsPipelineError(f"Missing required aplite XRF rows: {missing_rows}")

    work = work.set_index(sample_name_col).loc[row_order]
    rowwise = work.T.reset_index().rename(columns={"index": "SampleID"})
    if fe_total_row in rowwise.columns:
        rowwise = rowwise.rename(columns={fe_total_row: "FeOt"})
    else:
        raise RMeltsPipelineError(f"Expected Fe total row {fe_total_row!r} not found after transpose")

    dry_cols = [
        "SiO2",
        "TiO2",
        "Al2O3",
        "FeOt",
        "MnO",
        "MgO",
        "CaO",
        "Na2O",
        "K2O",
        "P2O5",
    ]
    for col in dry_cols:
        if col not in rowwise.columns:
            raise RMeltsPipelineError(f"Transposed row-wise aplite table missing required column: {col}")
        rowwise[col] = pd.to_numeric(rowwise[col], errors="coerce")

    rowwise["dry_total_original"] = rowwise[dry_cols].sum(axis=1)
    valid_total_mask = rowwise["dry_total_original"] > 0
    if not valid_total_mask.any():
        raise RMeltsPipelineError("All dry totals are <= 0 after transposing aplite XRF table")
    scale = (float(dry_normalize_to) / rowwise["dry_total_original"]).where(valid_total_mask)
    for col in dry_cols:
        rowwise[col] = rowwise[col] * scale
    rowwise["dry_total_normalized"] = rowwise[dry_cols].sum(axis=1)
    rowwise["H2O"] = float(fixed_h2o_wt)

    sample_labels = rowwise["SampleID"].astype(str).tolist()
    all_name = f"{xrf_csv_path.stem}_drynorm_rowwise_H2O{str(fixed_h2o_wt).replace('.', 'p')}.csv"
    all_path = norm_dir / all_name
    rowwise.to_csv(all_path, index=False)

    target_path: Optional[Path] = None
    target_list: Optional[list[str]] = None
    if target_samples is not None:
        target_list = [str(s) for s in target_samples]
        target_set = set(target_list)
        target_df = rowwise[rowwise["SampleID"].astype(str).isin(target_set)].copy()
        if target_df.empty:
            raise RMeltsPipelineError(f"None of target_samples found in aplite row-wise table: {target_list}")
        if len(target_df) == 1:
            target_name = f"{target_df.iloc[0]['SampleID']}_drynorm_one_row.csv"
        else:
            target_name = f"{dataset_name}_targets_drynorm_rowwise.csv"
        target_path = norm_dir / target_name
        target_df.to_csv(target_path, index=False)

    metadata = {
        "xrf_csv_path": str(xrf_csv_path),
        "sample_name_col": str(sample_name_col),
        "required_rows": [str(r) for r in required_rows],
        "fe_total_row": str(fe_total_row),
        "fixed_h2o_wt": float(fixed_h2o_wt),
        "dry_normalize_to": float(dry_normalize_to),
        "num_samples": int(len(rowwise)),
        "target_samples": None if target_list is None else target_list,
    }
    return ApliteNormalizationResult(
        normalized_all_rowwise_csv_path=str(all_path),
        normalized_target_rowwise_csv_path=None if target_path is None else str(target_path),
        num_samples=int(len(rowwise)),
        sample_labels=sample_labels,
        metadata=metadata,
    )


def _normalize_override_columns(override_df: Any) -> Any:
    rename_map: dict[str, str] = {}
    for col in list(override_df.columns):
        if col == "sample_label":
            continue
        canonical = RUN_PARAM_OVERRIDE_ALIAS.get(col, RUN_PARAM_OVERRIDE_ALIAS.get(str(col).lower()))
        if canonical:
            rename_map[col] = canonical
    if rename_map:
        override_df = override_df.rename(columns=rename_map)
    return override_df


def _build_melts_input_wide_dataframe(
    prepared_df: Any,
    base_params: MELTSRunParams,
    *,
    per_sample_override_df: Any = None,
) -> Any:
    pd = _require_pandas()
    prepared_df = _normalize_prepared_dataframe_columns(prepared_df)

    override_lookup = None
    if per_sample_override_df is not None:
        if "sample_label" not in per_sample_override_df.columns:
            raise RMeltsPipelineError("per_sample_param_overrides_csv must include 'sample_label'")
        per_sample_override_df = _normalize_override_columns(per_sample_override_df.copy())
        per_sample_override_df["sample_label"] = per_sample_override_df["sample_label"].astype(str)
        override_lookup = per_sample_override_df.set_index("sample_label", drop=False)

    wide_data: dict[str, list[Any]] = {}
    row_index = MELTS_ALL_ROWS
    base_row_map = base_params.to_melts_row_map()

    for _, rec in prepared_df.iterrows():
        sample_label = str(rec["sample_label"])
        column_values: list[Any] = []

        sample_row_params = dict(base_row_map)
        if override_lookup is not None and sample_label in override_lookup.index:
            override_row = override_lookup.loc[sample_label]
            # `loc` may return DataFrame if duplicates exist; keep first deterministically.
            if hasattr(override_row, "iloc") and hasattr(override_row, "ndim") and int(override_row.ndim) == 2:
                override_row = override_row.iloc[0]
            for key in RUN_PARAM_ROW_KEYS:
                if key in override_row.index:
                    raw = override_row[key]
                    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
                        continue
                    sample_row_params[key] = raw

        for row_name in row_index:
            if row_name in MELTS_OXIDE_ROWS:
                column_values.append(float(rec[row_name]))
            else:
                column_values.append(sample_row_params[row_name])

        wide_data[sample_label] = column_values

    wide_df = pd.DataFrame(wide_data, index=row_index)
    return wide_df


def _patch_helper_source_text_for_spawn_safety(
    source_text: str,
    *,
    patch_profile: Optional[Any] = None,
) -> str:
    """
    Patch a copied helper source (not Liam's original file) to avoid known
    hang paths in spawned pressure workers.
    """
    profile = _resolve_helper_patch_profile(patch_profile)

    if profile.fix_liquidus_timeout_loop:
        # 1) find_wet_liquidus timeout branch can loop forever because i is not
        # incremented on repeated timeouts and current_T can march upward forever.
        old = (
            "                current_T = current_T+25  # continue with higher temperature\n"
            "                continue\n"
        )
        new = (
            "                current_T = min(T1, current_T+25)  # continue with higher temperature (bounded)\n"
            "                i = i + 1  # avoid infinite loop on repeated timeout states\n"
            "                continue\n"
        )
        if old in source_text:
            source_text = source_text.replace(old, new, 1)

    if profile.wrap_main_execute_with_timeout:
        # 2) run_single_pressure_step temperature loop uses raw equil.execute with no
        # timeout; replace it with the helper's timeout-protected wrapper.
        continue_line = "                    continue\n"
        if profile.timeout_path_advances_temperature:
            continue_line = "                    temp -= delta_T\n                    continue\n"
        old_exec = (
            "                state = equil.execute(temp + 273.15, pressure * 10, bulk_comp=blk_cmp, con_deltaNNO=fO2_offset, debug=0)\n"
            "\n"
            "                t1 = time.time()\n"
            "                calc_time += (t1 - t0)\n"
        )
        new_exec = (
            "                state = safe_equilibrium_execute(equil, temp + 273.15, pressure * 10, timeout=10,\n"
            "                                              bulk_comp=blk_cmp, con_deltaNNO=fO2_offset, debug=0)\n"
            "                if state is None:\n"
            "                    print(f'Error at T={temp}, P={pressure}: timeout in execute')\n"
            f"{continue_line}"
            "\n"
            "                t1 = time.time()\n"
            "                calc_time += (t1 - t0)\n"
        )
        if old_exec in source_text:
            source_text = source_text.replace(old_exec, new_exec, 1)

    if profile.skip_wet_liquidus_presearch:
        # 3) Skip helper wet-liquidus pre-search in spawned pressure workers.
        liq_old = "        temp = find_wet_liquidus(equil, T1, T2, pressure, 50, blk_cmp, fO2_offset, verbose)\n"
        liq_new = "        temp = T1  # pipeline patch: skip wet-liquidus pre-search for robustness\n"
        if liq_old in source_text:
            source_text = source_text.replace(liq_old, liq_new, 1)
    return source_text


def _prepare_run_local_helper_copy(
    helper_dir: Path,
    run_dir: Path,
    *,
    patch_profile: Optional[Any] = None,
) -> Path:
    """
    Create a run-scoped helper copy so multiprocessing spawn workers import the
    patched helper from the run directory without modifying Liam's source file.
    """
    helper_src = helper_dir / "MeltsHelperFunctions.py"
    if not helper_src.exists():
        raise FileNotFoundError(f"Helper module not found: {helper_src}")
    helper_dst = run_dir / "MeltsHelperFunctions.py"
    text = helper_src.read_text(encoding="utf-8")
    helper_dst.write_text(
        _patch_helper_source_text_for_spawn_safety(text, patch_profile=patch_profile),
        encoding="utf-8",
    )
    return helper_dst


def _load_helper_module(
    helper_dir: Path,
    *,
    run_dir: Optional[Path] = None,
    patch_profile: Optional[Any] = None,
    patch_pressure_calc: bool = True,
) -> Any:
    helper_path = helper_dir / "MeltsHelperFunctions.py"
    if not helper_path.exists():
        raise FileNotFoundError(f"Helper module not found: {helper_path}")

    import_path = helper_path
    if run_dir is not None:
        run_dir = Path(run_dir).expanduser().resolve()
        _ensure_dir(run_dir)
        import_path = _prepare_run_local_helper_copy(helper_dir, run_dir, patch_profile=patch_profile)
        run_dir_str = str(run_dir)
        if run_dir_str not in sys.path:
            sys.path.insert(0, run_dir_str)

    # Use a stable canonical module name so multiprocessing spawn workers can re-import
    # helper functions defined in this module (e.g., process_single_composition_parallel).
    module_name = "MeltsHelperFunctions"
    helper_dir_str = str(helper_dir)
    if helper_dir_str not in sys.path:
        sys.path.insert(0, helper_dir_str)

    spec = importlib.util.spec_from_file_location(module_name, str(import_path))
    if spec is None or spec.loader is None:
        raise RMeltsPipelineError(f"Could not create import spec for {import_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        # Keep module in sys.modules for imported dependencies during execution, but do not override canonical name.
        pass
    if patch_pressure_calc:
        return _patch_helper_module_pressure_calc(module)
    return module


def _fit_pressure_residual_vertex(pressures: list[float], residuals: list[float]) -> tuple[Optional[float], Optional[float], tuple[float, float, float]]:
    np = _require_dependency("numpy")
    valid = [(float(p), float(r)) for p, r in zip(pressures, residuals) if _safe_float(p) is not None and _safe_float(r) is not None]
    if len(valid) < 3:
        return None, None, (math.nan, math.nan, math.nan)
    P = np.array([v[0] for v in valid], dtype=float)
    R = np.array([v[1] for v in valid], dtype=float)
    idx_min = int(np.nanargmin(R))
    start = max(0, idx_min - 2)
    stop = min(len(P), idx_min + 3)
    P_fit = P[start:stop]
    R_fit = R[start:stop]
    if len(P_fit) < 3:
        return None, None, (math.nan, math.nan, math.nan)
    try:
        a, b, c = np.polyfit(P_fit, R_fit, deg=2)
    except Exception:
        return None, None, (math.nan, math.nan, math.nan)
    if not np.isfinite(a) or not np.isfinite(b) or not np.isfinite(c) or abs(a) < 1e-12:
        return None, None, (float(a), float(b), float(c))
    p_est = float(-b / (2.0 * a))
    r_min = float(a * p_est * p_est + b * p_est + c)
    return p_est, r_min, (float(a), float(b), float(c))


def _get_first_temperature_at_pressure(df: Any, pressure: float, pressure_tol: float = 1e-6) -> Optional[float]:
    pd = _require_pandas()
    if df is None or df.empty or "P (MPa)" not in df.columns or "T (C)" not in df.columns:
        return None
    pcol = pd.to_numeric(df["P (MPa)"], errors="coerce")
    tcol = pd.to_numeric(df["T (C)"], errors="coerce")
    mask = (pcol - float(pressure)).abs() <= float(pressure_tol)
    if not mask.any():
        return None
    vals = tcol[mask]
    if vals.empty:
        return None
    return _safe_float(vals.iloc[0])


def _read_pressure_analysis_sheet_from_workbook(wb: Any) -> dict[str, Any]:
    defaults = _pressure_analysis_row_defaults()
    out = dict(defaults)
    sheet_name = None
    for s in wb.sheetnames:
        if str(s).strip().lower() == "pressure analysis":
            sheet_name = s
            break
    if sheet_name is None:
        out["pressure_calc_error"] = "Pressure Analysis sheet not found"
        return out
    ws = wb[sheet_name]
    rows = list(ws.values)
    if len(rows) < 2:
        out["pressure_calc_error"] = "Pressure Analysis sheet empty"
        return out

    parsed_any = False
    for row in rows[1:]:
        if not row or len(row) < 2:
            continue
        phase_sys = str(row[0]).strip().lower() if row[0] is not None else ""
        p_est = _safe_float(row[1] if len(row) > 1 else None)
        r_min = _safe_float(row[2] if len(row) > 2 else None)
        a = _safe_float(row[3] if len(row) > 3 else None)
        b = _safe_float(row[4] if len(row) > 4 else None)
        c = _safe_float(row[5] if len(row) > 5 else None)
        if phase_sys.startswith("2-phase"):
            out["P_2phase_qtz_fsp_MPa"] = p_est
            out["Rmin_2phase_qtz_fsp_C"] = r_min
            out["fit_a_2phase"] = a
            out["fit_b_2phase"] = b
            out["fit_c_2phase"] = c
            parsed_any = True
        elif phase_sys.startswith("3-phase"):
            out["P_3phase_qtz_fsp_fsp1_MPa"] = p_est
            out["Rmin_3phase_qtz_fsp_fsp1_C"] = r_min
            out["fit_a_3phase"] = a
            out["fit_b_3phase"] = b
            out["fit_c_3phase"] = c
            parsed_any = True
    out["pressure_calc_method"] = "helper_pressure_analysis_sheet" if parsed_any else "helper_pressure_analysis_sheet_unparsed"
    if parsed_any:
        out["pressure_calc_error"] = None
    return out


def _compute_pressure_analysis_from_workbook(
    wb: Any,
    *,
    residual_threshold: float = 5.0,
    prefer_existing_sheet: bool = True,
) -> dict[str, Any]:
    pd = _require_pandas()
    out = dict(_pressure_analysis_row_defaults())

    sheet_name_map = {str(s).lower(): str(s) for s in wb.sheetnames}
    out["phase_sheet_quartz_present"] = "quartz" in sheet_name_map
    out["phase_sheet_feldspar_present"] = "feldspar" in sheet_name_map
    out["phase_sheet_feldspar_1_present"] = "feldspar_1" in sheet_name_map

    if prefer_existing_sheet and "pressure analysis" in sheet_name_map:
        parsed = _read_pressure_analysis_sheet_from_workbook(wb)
        parsed["phase_sheet_quartz_present"] = out["phase_sheet_quartz_present"]
        parsed["phase_sheet_feldspar_present"] = out["phase_sheet_feldspar_present"]
        parsed["phase_sheet_feldspar_1_present"] = out["phase_sheet_feldspar_1_present"]
        return parsed

    phase_dfs: dict[str, Any] = {}
    for phase in ["quartz", "feldspar", "feldspar_1"]:
        sname = sheet_name_map.get(phase)
        if sname is None:
            phase_dfs[phase] = pd.DataFrame()
        else:
            phase_dfs[phase] = _sheet_to_dataframe(wb, sname)

    pressures_set: set[float] = set()
    for phase_df in phase_dfs.values():
        if phase_df.empty or "P (MPa)" not in phase_df.columns:
            continue
        pvals = pd.to_numeric(phase_df["P (MPa)"], errors="coerce").dropna().tolist()
        pressures_set.update(float(p) for p in pvals)
    if not pressures_set:
        out["pressure_calc_method"] = "pipeline_fallback"
        out["pressure_calc_error"] = "No pressure values found in quartz/feldspar sheets"
        return out
    pressures_desc = sorted(pressures_set, reverse=True)

    qz_t = [_get_first_temperature_at_pressure(phase_dfs["quartz"], p) for p in pressures_desc]
    fsp_t = [_get_first_temperature_at_pressure(phase_dfs["feldspar"], p) for p in pressures_desc]
    fsp1_t = [_get_first_temperature_at_pressure(phase_dfs["feldspar_1"], p) for p in pressures_desc]

    res2: list[Optional[float]] = []
    res3: list[Optional[float]] = []
    for t_qz, t_fsp, t_fsp1 in zip(qz_t, fsp_t, fsp1_t):
        if t_qz is None or t_fsp is None:
            res2.append(None)
        else:
            res2.append(abs(float(t_qz) - float(t_fsp)))
        if t_qz is None or t_fsp is None or t_fsp1 is None:
            res3.append(None)
        else:
            res3.append(max(t_qz, t_fsp, t_fsp1) - min(t_qz, t_fsp, t_fsp1))

    res2_arr = [math.nan if v is None else float(v) for v in res2]
    res3_arr = [math.nan if v is None else float(v) for v in res3]
    p2, r2, coeff2 = _fit_pressure_residual_vertex(pressures_desc, res2_arr)
    p3, r3, coeff3 = _fit_pressure_residual_vertex(pressures_desc, res3_arr)

    # Match Liam's acceptance behavior: threshold is applied to the raw sampled
    # residual minimum prior to parabola fitting, while we still report fitted
    # residuals for diagnostics/output.
    raw_r2_min = None
    raw_r3_min = None
    valid_res2 = [float(v) for v in res2 if v is not None]
    valid_res3 = [float(v) for v in res3 if v is not None]
    if valid_res2:
        raw_r2_min = min(valid_res2)
    if valid_res3:
        raw_r3_min = min(valid_res3)

    if raw_r2_min is not None and raw_r2_min <= float(residual_threshold):
        out["P_2phase_qtz_fsp_MPa"] = p2
        out["Rmin_2phase_qtz_fsp_C"] = r2
        out["fit_a_2phase"], out["fit_b_2phase"], out["fit_c_2phase"] = coeff2
    else:
        # Keep residual even when above threshold for diagnostics.
        out["Rmin_2phase_qtz_fsp_C"] = r2
        out["fit_a_2phase"], out["fit_b_2phase"], out["fit_c_2phase"] = coeff2
    if raw_r3_min is not None and raw_r3_min <= float(residual_threshold):
        out["P_3phase_qtz_fsp_fsp1_MPa"] = p3
        out["Rmin_3phase_qtz_fsp_fsp1_C"] = r3
        out["fit_a_3phase"], out["fit_b_3phase"], out["fit_c_3phase"] = coeff3
    else:
        out["Rmin_3phase_qtz_fsp_fsp1_C"] = r3
        out["fit_a_3phase"], out["fit_b_3phase"], out["fit_c_3phase"] = coeff3

    out["pressure_calc_method"] = "pipeline_fallback"
    if (out["P_2phase_qtz_fsp_MPa"] is None) and (out["P_3phase_qtz_fsp_fsp1_MPa"] is None):
        out["pressure_calc_error"] = "No acceptable 2-phase/3-phase pressure fit (or missing phase coverage)"
    return out


def _upsert_pressure_analysis_sheet_in_workbook(wb: Any, analysis: dict[str, Any]) -> None:
    # Works on normal (writeable) workbooks used during helper execution.
    target_name = None
    for s in wb.sheetnames:
        if str(s).strip().lower() == "pressure analysis":
            target_name = s
            break
    if target_name is not None:
        ws = wb[target_name]
        # Clear a small rectangular area used by this summary.
        for row in ws.iter_rows(min_row=1, max_row=8, min_col=1, max_col=7):
            for cell in row:
                cell.value = None
    else:
        ws = wb.create_sheet("Pressure Analysis")

    headers = [
        "Phase System",
        "Estimated Pressure (MPa)",
        "Minimum Residual (°C)",
        "a (quadratic)",
        "b (linear)",
        "c (constant)",
        "Method",
    ]
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)

    rows = [
        ("2-Phase (Qtz+Fsp)", "P_2phase_qtz_fsp_MPa", "Rmin_2phase_qtz_fsp_C", "fit_a_2phase", "fit_b_2phase", "fit_c_2phase"),
        ("3-Phase (Qtz+Fsp+Fsp1)", "P_3phase_qtz_fsp_fsp1_MPa", "Rmin_3phase_qtz_fsp_fsp1_C", "fit_a_3phase", "fit_b_3phase", "fit_c_3phase"),
    ]
    for r_idx, (label, pk, rk, ak, bk, ck) in enumerate(rows, 2):
        ws.cell(row=r_idx, column=1, value=label)
        p_val = analysis.get(pk)
        ws.cell(row=r_idx, column=2, value="Fit Failed" if p_val is None else p_val)
        ws.cell(row=r_idx, column=3, value=analysis.get(rk))
        ws.cell(row=r_idx, column=4, value=analysis.get(ak))
        ws.cell(row=r_idx, column=5, value=analysis.get(bk))
        ws.cell(row=r_idx, column=6, value=analysis.get(ck))
        ws.cell(row=r_idx, column=7, value=analysis.get("pressure_calc_method"))

    ws.cell(row=5, column=1, value="Notes")
    ws.cell(row=5, column=2, value=analysis.get("pressure_calc_error"))


def _patch_helper_module_pressure_calc(module: Any) -> Any:
    original = getattr(module, "pressure_calc", None)
    if original is None or getattr(module, "_rmelts_pressure_calc_patched", False):
        return module
    module._rmelts_pressure_calc_times_queue = []
    module._rmelts_pressure_residual_threshold_C = _safe_float(
        getattr(module, "_rmelts_pressure_residual_threshold_C", None)
    ) or 10.0

    def patched_pressure_calc(*args, **kwargs):
        t0 = time.time()
        kwargs.setdefault("embed_plot", False)
        residual_threshold = _safe_float(getattr(module, "_rmelts_pressure_residual_threshold_C", None)) or 10.0
        kwargs.setdefault("residual_threshold", float(residual_threshold))
        try:
            wb_out, p2, p3 = original(*args, **kwargs)
        except Exception:
            wb = kwargs.get("wb")
            if wb is None and len(args) >= 2:
                wb = args[1]
            filename = kwargs.get("filename")
            if wb is None and len(args) >= 1:
                filename = args[0]
            if wb is None:
                # If only filename is supplied, let original error propagate to avoid ambiguous rewrites.
                raise
            analysis = _compute_pressure_analysis_from_workbook(
                wb,
                prefer_existing_sheet=False,
                residual_threshold=float(residual_threshold),
            )
            _upsert_pressure_analysis_sheet_in_workbook(wb, analysis)
            p2 = (
                analysis.get("P_2phase_qtz_fsp_MPa"),
                analysis.get("Rmin_2phase_qtz_fsp_C"),
                (
                    analysis.get("fit_a_2phase", math.nan),
                    analysis.get("fit_b_2phase", math.nan),
                    analysis.get("fit_c_2phase", math.nan),
                ),
            )
            p3 = (
                analysis.get("P_3phase_qtz_fsp_fsp1_MPa"),
                analysis.get("Rmin_3phase_qtz_fsp_fsp1_C"),
                (
                    analysis.get("fit_a_3phase", math.nan),
                    analysis.get("fit_b_3phase", math.nan),
                    analysis.get("fit_c_3phase", math.nan),
                ),
            )
            elapsed = time.time() - t0
            try:
                module._rmelts_pressure_calc_times_queue.append(float(elapsed))
            except Exception:
                pass
            return wb, p2, p3

        wb_for_parse = kwargs.get("wb")
        if wb_for_parse is None and len(args) >= 2:
            wb_for_parse = args[1]
        if wb_for_parse is not None:
            try:
                analysis = _compute_pressure_analysis_from_workbook(
                    wb_for_parse,
                    prefer_existing_sheet=True,
                    residual_threshold=float(residual_threshold),
                )
                _upsert_pressure_analysis_sheet_in_workbook(wb_for_parse, analysis)
                p2 = (
                    analysis.get("P_2phase_qtz_fsp_MPa"),
                    analysis.get("Rmin_2phase_qtz_fsp_C"),
                    (
                        analysis.get("fit_a_2phase", math.nan),
                        analysis.get("fit_b_2phase", math.nan),
                        analysis.get("fit_c_2phase", math.nan),
                    ),
                )
                p3 = (
                    analysis.get("P_3phase_qtz_fsp_fsp1_MPa"),
                    analysis.get("Rmin_3phase_qtz_fsp_fsp1_C"),
                    (
                        analysis.get("fit_a_3phase", math.nan),
                        analysis.get("fit_b_3phase", math.nan),
                        analysis.get("fit_c_3phase", math.nan),
                    ),
                )
            except Exception:
                pass
        elapsed = time.time() - t0
        try:
            module._rmelts_pressure_calc_times_queue.append(float(elapsed))
        except Exception:
            pass
        return wb_out, p2, p3

    module.pressure_calc = patched_pressure_calc
    module._rmelts_pressure_calc_patched = True
    return module


def _run_helper_import_backend(
    *,
    helper_dir: Path,
    melts_input_csv_path: Path,
    run_dir: Path,
    max_composition_workers: int,
    max_pressure_workers: int,
    verbose: bool,
    pressure_residual_threshold_C: float = 10.0,
) -> list[dict[str, Any]]:
    module = _load_helper_module(helper_dir, run_dir=run_dir)
    try:
        module._rmelts_pressure_residual_threshold_C = float(pressure_residual_threshold_C)
    except Exception:
        module._rmelts_pressure_residual_threshold_C = 10.0
    if not hasattr(module, "parallel_melts_main_loop"):
        raise RMeltsPipelineError("Helper module does not expose parallel_melts_main_loop")

    with _pushd(run_dir):
        results = module.parallel_melts_main_loop(  # type: ignore[attr-defined]
            str(melts_input_csv_path),
            int(max_composition_workers),
            int(max_pressure_workers),
            bool(verbose),
        )
    if results is None:
        return []
    if not isinstance(results, list):
        raise RMeltsPipelineError(f"Unexpected helper return type: {type(results)}")
    normalized_results = [dict(r) if isinstance(r, dict) else {"raw_result": r} for r in results]

    # Best-effort runtime enrichment from pipeline wrapper instrumentation.
    queue = getattr(module, "_rmelts_pressure_calc_times_queue", None)
    if isinstance(queue, list):
        qidx = 0
        for r in normalized_results:
            if not isinstance(r, dict):
                continue
            r.setdefault("runtime_log_version", "rmp_runtime_v1")
            calc_time = _safe_float(r.get("calc_time"))
            total_time = _safe_float(r.get("total_time"))
            r.setdefault("melts_calc_time", calc_time)
            if ("error" in r and r.get("error") not in (None, "")) or r.get("filename") in (None, ""):
                r.setdefault("pressure_calc_time", None)
                r.setdefault("workbook_build_time", None)
                continue
            ptime = None
            if qidx < len(queue):
                ptime = _safe_float(queue[qidx])
                qidx += 1
            r.setdefault("pressure_calc_time", ptime)
            wb_build = None
            if total_time is not None and calc_time is not None and ptime is not None:
                wb_build = max(float(total_time) - float(calc_time) - float(ptime), 0.0)
            r.setdefault("workbook_build_time", wb_build)

    return normalized_results


def _run_helper_cli_backend(
    *,
    helper_dir: Path,
    melts_input_csv_path: Path,
    run_dir: Path,
    max_composition_workers: int,
    max_pressure_workers: int,
    verbose: bool,
) -> list[dict[str, Any]]:
    script_path = helper_dir / "melts-parallel.py"
    if not script_path.exists():
        raise FileNotFoundError(f"CLI helper script not found: {script_path}")

    cmd = [
        sys.executable,
        str(script_path),
        "--csv_filename",
        str(melts_input_csv_path),
        "--max_composition_workers",
        str(int(max_composition_workers)),
        "--max_pressure_workers",
        str(int(max_pressure_workers)),
    ]
    if bool(verbose):
        cmd.append("--verbose")

    proc = subprocess.run(
        cmd,
        cwd=str(run_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RMeltsPipelineError(
            f"CLI helper run failed (code {proc.returncode}). stderr:\n{proc.stderr.strip()}"
        )

    # CLI script does not return structured results; infer from generated workbooks.
    inferred: list[dict[str, Any]] = []
    for path in sorted((run_dir / "parallel-results").glob("**/*.xlsx")):
        m = re.match(r"Parallel_MELTS_(.+?)_dP", path.name)
        label = m.group(1) if m else path.stem
        inferred.append(
            {
                "label": label,
                "filename": str(path.relative_to(run_dir)),
                "num_pressure_steps": None,
                "num_data_points": None,
                "P_QF": None,
                "P_Q2F": None,
                "total_time": None,
                "calc_time": None,
            }
        )
    return inferred


def _create_manifest_from_results(
    *,
    dataset_name: str,
    run_dir: Path,
    melts_input_csv_path: Path,
    expected_labels: list[str],
    results: list[dict[str, Any]],
    pressure_residual_threshold_C: float = 10.0,
) -> Any:
    pd = _require_pandas()
    openpyxl = _require_openpyxl()

    result_by_label: dict[str, dict[str, Any]] = {}
    for r in results:
        label = str(r.get("label", ""))
        if not label:
            continue
        result_by_label[label] = r

    manifest_rows: list[dict[str, Any]] = []
    for label in expected_labels:
        r = result_by_label.get(label)
        if r is None:
            manifest_rows.append(
                {
                    **_pressure_analysis_row_defaults(),
                    **_runtime_row_defaults(),
                    "dataset_name": dataset_name,
                    "sample_label": label,
                    "status": "error",
                    "excel_path": "",
                    "helper_filename": "",
                    "num_pressure_steps": None,
                    "num_data_points": None,
                    "P_QF": None,
                    "P_Q2F": None,
                    "total_time_s": None,
                    "calc_time_s": None,
                    "melts_calc_time_s": None,
                    "pressure_calc_time_s": None,
                    "workbook_build_time_s": None,
                    "runtime_log_version": "rmp_runtime_v1",
                    "pressure_residual_threshold_C": float(pressure_residual_threshold_C),
                    "error": "No helper result returned for sample",
                    "run_dir": str(run_dir),
                    "melts_input_csv_path": str(melts_input_csv_path),
                }
            )
            continue

        helper_filename = r.get("filename")
        excel_path = ""
        if helper_filename:
            candidate = (run_dir / str(helper_filename)).resolve()
            excel_path = str(candidate)

        has_error = "error" in r and r.get("error") not in (None, "")
        row_data = {
            **_pressure_analysis_row_defaults(),
            **_runtime_row_defaults(),
            "dataset_name": dataset_name,
            "sample_label": label,
            "status": "error" if has_error else "success",
            "excel_path": excel_path,
            "helper_filename": "" if helper_filename is None else str(helper_filename),
            "num_pressure_steps": r.get("num_pressure_steps"),
            "num_data_points": r.get("num_data_points"),
            "P_QF": _safe_float(r.get("P_QF")),
            "P_Q2F": _safe_float(r.get("P_Q2F")),
            "total_time_s": _safe_float(r.get("total_time")),
            "calc_time_s": _safe_float(r.get("calc_time", r.get("melts_calc_time"))),
            "melts_calc_time_s": _safe_float(r.get("melts_calc_time", r.get("calc_time"))),
            "pressure_calc_time_s": _safe_float(r.get("pressure_calc_time")),
            "workbook_build_time_s": _safe_float(r.get("workbook_build_time")),
            "runtime_log_version": r.get("runtime_log_version", "rmp_runtime_v1"),
            "pressure_residual_threshold_C": float(pressure_residual_threshold_C),
            "error": r.get("error", ""),
            "run_dir": str(run_dir),
            "melts_input_csv_path": str(melts_input_csv_path),
        }

        if (not has_error) and excel_path:
            try:
                wb = openpyxl.load_workbook(excel_path, data_only=True, read_only=True)
                try:
                    pa = _compute_pressure_analysis_from_workbook(
                        wb,
                        prefer_existing_sheet=True,
                        residual_threshold=float(pressure_residual_threshold_C),
                    )
                finally:
                    try:
                        wb.close()
                    except Exception:
                        pass
                row_data.update(pa)
            except Exception as exc:
                row_data["pressure_calc_method"] = "manifest_parse_failed"
                row_data["pressure_calc_error"] = str(exc)

        manifest_rows.append(
            {
                **row_data,
            }
        )

    manifest_df = pd.DataFrame(manifest_rows)
    required_cols = [
        "dataset_name",
        "sample_label",
        "status",
        "excel_path",
        "helper_filename",
        "num_pressure_steps",
        "num_data_points",
        "P_QF",
        "P_Q2F",
        "melts_calc_time_s",
        "pressure_calc_time_s",
        "workbook_build_time_s",
        "runtime_log_version",
        "pressure_residual_threshold_C",
        "P_2phase_qtz_fsp_MPa",
        "Rmin_2phase_qtz_fsp_C",
        "fit_a_2phase",
        "fit_b_2phase",
        "fit_c_2phase",
        "P_3phase_qtz_fsp_fsp1_MPa",
        "Rmin_3phase_qtz_fsp_fsp1_C",
        "fit_a_3phase",
        "fit_b_3phase",
        "fit_c_3phase",
        "pressure_calc_method",
        "pressure_calc_error",
        "phase_sheet_quartz_present",
        "phase_sheet_feldspar_present",
        "phase_sheet_feldspar_1_present",
        "total_time_s",
        "calc_time_s",
        "error",
        "run_dir",
        "melts_input_csv_path",
    ]
    manifest_df = manifest_df[required_cols]
    return manifest_df


def rMELTS_run(
    prepared_mc_csv_path,
    *,
    output_dir,
    dataset_name,
    T1,
    T2,
    dT,
    P1,
    P2,
    dP,
    fO2_constraint,
    fO2_buffer,
    fO2_offset,
    model="rhyolite-MELTS_v1.0.x",
    calculation="QF_P_Calc",
    max_composition_workers=1,
    max_pressure_workers=4,
    verbose=False,
    helper_dir="/Users/lopezama/Downloads",
    backend="import",
    per_sample_param_overrides_csv=None,
    pressure_residual_threshold_C=10.0,
):
    """Generate helper-compatible MELTS input CSV, run parallel rhyolite-MELTS, and write a manifest."""
    pd = _require_pandas()

    dataset_name = _sanitize_dataset_name(str(dataset_name))
    output_dir = Path(output_dir).expanduser().resolve()
    helper_dir = Path(helper_dir).expanduser().resolve()
    prepared_mc_csv_path = Path(prepared_mc_csv_path).expanduser().resolve()

    base_params = MELTSRunParams(
        T1=float(T1),
        T2=float(T2),
        dT=float(dT),
        P1=float(P1),
        P2=float(P2),
        dP=float(dP),
        fO2_constraint=str(fO2_constraint),
        fO2_buffer=str(fO2_buffer),
        fO2_offset=float(fO2_offset),
        model=str(model),
        calculation=str(calculation),
    )
    _validate_run_params(base_params)

    run_dir = _ensure_dir(output_dir / dataset_name / "runs" / _now_run_id())
    melts_input_csv_path = run_dir / "melts_input.csv"
    manifest_csv_path = run_dir / "run_manifest.csv"
    metadata_json_path = run_dir / "run_metadata.json"

    prepared_df = pd.read_csv(prepared_mc_csv_path)
    prepared_df = _normalize_prepared_dataframe_columns(prepared_df)
    expected_labels = prepared_df["sample_label"].astype(str).tolist()

    override_df = None
    if per_sample_param_overrides_csv is not None:
        override_df = pd.read_csv(per_sample_param_overrides_csv)

    wide_df = _build_melts_input_wide_dataframe(
        prepared_df,
        base_params,
        per_sample_override_df=override_df,
    )
    wide_df.to_csv(melts_input_csv_path, index=True)

    start = time.time()
    results: list[dict[str, Any]] = []
    backend_used = str(backend)
    backend_error: Optional[str] = None

    try:
        if backend == "import":
            results = _run_helper_import_backend(
                helper_dir=helper_dir,
                melts_input_csv_path=melts_input_csv_path,
                run_dir=run_dir,
                max_composition_workers=int(max_composition_workers),
                max_pressure_workers=int(max_pressure_workers),
                verbose=bool(verbose),
                pressure_residual_threshold_C=float(pressure_residual_threshold_C),
            )
        elif backend == "cli_fallback":
            try:
                results = _run_helper_import_backend(
                    helper_dir=helper_dir,
                    melts_input_csv_path=melts_input_csv_path,
                    run_dir=run_dir,
                    max_composition_workers=int(max_composition_workers),
                    max_pressure_workers=int(max_pressure_workers),
                    verbose=bool(verbose),
                    pressure_residual_threshold_C=float(pressure_residual_threshold_C),
                )
                backend_used = "import"
            except Exception as exc:
                backend_error = f"Import backend failed, falling back to CLI: {exc}"
                results = _run_helper_cli_backend(
                    helper_dir=helper_dir,
                    melts_input_csv_path=melts_input_csv_path,
                    run_dir=run_dir,
                    max_composition_workers=int(max_composition_workers),
                    max_pressure_workers=int(max_pressure_workers),
                    verbose=bool(verbose),
                )
                backend_used = "cli"
        else:
            raise ValueError("backend must be 'import' or 'cli_fallback'")
    except Exception as exc:
        # Total backend failure: emit an all-error manifest.
        backend_error = str(exc)
        results = [{"label": label, "error": str(exc)} for label in expected_labels]

    elapsed = time.time() - start

    manifest_df = _create_manifest_from_results(
        dataset_name=dataset_name,
        run_dir=run_dir,
        melts_input_csv_path=melts_input_csv_path,
        expected_labels=expected_labels,
        results=results,
        pressure_residual_threshold_C=float(pressure_residual_threshold_C),
    )
    manifest_df.to_csv(manifest_csv_path, index=False)

    # Pipeline-owned per-sample runtime/pressure log line (distinct from Liam helper logs).
    for _, mrow in manifest_df.iterrows():
        phase_names = []
        if bool(mrow.get("phase_sheet_quartz_present")):
            phase_names.append("qz")
        if bool(mrow.get("phase_sheet_feldspar_present")):
            phase_names.append("fsp")
        if bool(mrow.get("phase_sheet_feldspar_1_present")):
            phase_names.append("fsp1")
        phase_str = ",".join(phase_names) if phase_names else "-"
        print(
            "sample={sample} status={status} melts_calc_s={melts} pressure_calc_s={pcalc} total_s={total} "
            "p2_qtz_fsp={p2} p3_qtz_fsp_fsp1={p3} phases={phases}".format(
                sample=mrow.get("sample_label"),
                status=mrow.get("status"),
                melts=mrow.get("melts_calc_time_s"),
                pcalc=mrow.get("pressure_calc_time_s"),
                total=mrow.get("total_time_s"),
                p2=mrow.get("P_2phase_qtz_fsp_MPa"),
                p3=mrow.get("P_3phase_qtz_fsp_fsp1_MPa"),
                phases=phase_str,
            )
        )

    summary = {
        "dataset_name": dataset_name,
        "num_samples": int(len(expected_labels)),
        "num_success": int((manifest_df["status"] == "success").sum()),
        "num_error": int((manifest_df["status"] == "error").sum()),
        "elapsed_wall_time_s": elapsed,
        "pressure_residual_threshold_C": float(pressure_residual_threshold_C),
        "backend_requested": str(backend),
        "backend_used": backend_used,
        "backend_error": backend_error,
        "run_dir": str(run_dir),
    }

    metadata = {
        "summary": summary,
        "run_params": _json_safe(asdict(base_params)),
        "paths": {
            "prepared_mc_csv_path": str(prepared_mc_csv_path),
            "melts_input_csv_path": str(melts_input_csv_path),
            "manifest_csv_path": str(manifest_csv_path),
            "helper_dir": str(helper_dir),
        },
        "results": _json_safe(results),
        "manifest_preview": _json_safe(manifest_df.to_dict(orient="records")),
    }
    metadata_json_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return RunResult(
        melts_input_csv_path=str(melts_input_csv_path),
        run_dir=str(run_dir),
        manifest_csv_path=str(manifest_csv_path),
        results=results,
        summary=summary,
    )


def _phase_request_set(phases: Iterable[str]) -> list[str]:
    out = []
    seen = set()
    for p in phases:
        if p is None:
            continue
        s = str(p).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _sheet_to_dataframe(wb: Any, sheet_name: str) -> Any:
    pd = _require_pandas()
    ws = wb[sheet_name]
    rows = list(ws.values)
    if not rows:
        return pd.DataFrame()
    headers = list(rows[0])
    data = list(rows[1:])
    # Drop completely blank trailing columns.
    if headers:
        keep_positions = [
            i for i, h in enumerate(headers)
            if h is not None and str(h).strip() != ""
        ]
        if keep_positions:
            headers = [headers[i] for i in keep_positions]
            data = [[row[i] if i < len(row) else None for i in keep_positions] for row in data]
    return pd.DataFrame(data, columns=headers)


def _standardize_sheet_columns(df: Any) -> Any:
    pd = _require_pandas()
    if df.empty:
        return df.copy()

    rename_map = {
        "T (C)": "T_C",
        "P (MPa)": "P_MPa",
        "G (kJ)": "G_kJ",
        "H (kJ)": "H_kJ",
        "S (J/K)": "S_J_per_K",
        "Cp (J/K)": "Cp_J_per_K",
        "V (cm3)": "V_cm3",
        "mass (g)": "Mass_g",
        "rho (g/cm3)": "Density_g_cm3",
        "Outcome": "Outcome",
        "Index": "Index",
        "deltaQFM": "deltaQFM",
        "alpha (1/K)": "alpha_1_per_K",
        "beta (1/bar)": "beta_1_per_bar",
    }
    standardized = df.rename(columns=rename_map).copy()

    oxide_rename: dict[str, str] = {}
    for c in standardized.columns:
        if not isinstance(c, str):
            continue
        m = re.match(r"^\s*([^()]+?)\s*\(wt%\)\s*$", c)
        if m:
            oxide = m.group(1).strip().replace(" ", "")
            oxide_rename[c] = f"{oxide}_wt_pct"
    if oxide_rename:
        standardized = standardized.rename(columns=oxide_rename)

    # Normalize key numeric columns when present.
    for col in ["T_C", "P_MPa", "G_kJ", "H_kJ", "S_J_per_K", "Cp_J_per_K", "V_cm3", "Mass_g", "Density_g_cm3"]:
        if col in standardized.columns:
            standardized[col] = pd.to_numeric(standardized[col], errors="coerce")
    return standardized


def _apply_temperature_pressure_filters(df: Any, temperature_range, pressure_range) -> Any:
    if df.empty:
        return df
    out = df
    if temperature_range is not None and "T_C" in out.columns:
        tmin, tmax = temperature_range
        if tmin is not None:
            out = out[out["T_C"] >= float(tmin)]
        if tmax is not None:
            out = out[out["T_C"] <= float(tmax)]
    if pressure_range is not None and "P_MPa" in out.columns:
        pmin, pmax = pressure_range
        if pmin is not None:
            out = out[out["P_MPa"] >= float(pmin)]
        if pmax is not None:
            out = out[out["P_MPa"] <= float(pmax)]
    return out


def _make_phase_placeholder_row(sample_label: str, source_excel: str, phase: str, raw_sheet_name: Optional[str]) -> dict[str, Any]:
    return {
        "sample_label": sample_label,
        "source_excel": source_excel,
        "phase": phase,
        "phase_sheet_name": raw_sheet_name,
        "T_C": None,
        "P_MPa": None,
        "G_kJ": None,
        "H_kJ": None,
        "S_J_per_K": None,
        "Cp_J_per_K": None,
        "V_cm3": None,
        "Mass_g": None,
        "Density_g_cm3": None,
        "Outcome": None,
    }


def rMELTS_geobarometry_basis(
    manifest_csv_path,
    *,
    phases,
    samples=None,
    include_system=True,
    include_liquid=True,
    columns=None,
    temperature_range=None,
    pressure_range=None,
    missing_phase_policy="skip_point",
    interpolation_grid=None,
):
    """
    Extract selected sheets/phases from MELTS workbook outputs into analysis-ready tables.

    Stage 1 scope: data preparation only (no minimization/objective calculation).
    """
    pd = _require_pandas()
    openpyxl = _require_openpyxl()

    if missing_phase_policy not in {"skip_point", "skip_sample", "fill_nan", "error"}:
        raise ValueError("missing_phase_policy must be one of: skip_point, skip_sample, fill_nan, error")

    manifest_csv_path = Path(manifest_csv_path).expanduser().resolve()
    manifest_df = pd.read_csv(manifest_csv_path)
    if manifest_df.empty:
        raise RMeltsPipelineError(f"Manifest is empty: {manifest_csv_path}")

    for col in ["sample_label", "status", "excel_path"]:
        if col not in manifest_df.columns:
            raise RMeltsPipelineError(f"Manifest missing required column: {col}")

    manifest_df["sample_label"] = manifest_df["sample_label"].astype(str)
    manifest_df["status"] = manifest_df["status"].astype(str)
    work_df = manifest_df[manifest_df["status"].str.lower() == "success"].copy()

    if samples is not None:
        requested_samples = {str(s) for s in samples}
        work_df = work_df[work_df["sample_label"].isin(requested_samples)].copy()

    requested_phase_names = _phase_request_set(phases)
    requested_lookup = [p.lower() for p in requested_phase_names]
    if include_system and "system" not in requested_lookup:
        requested_phase_names.insert(0, "system")
        requested_lookup.insert(0, "system")
    if include_liquid and "liquid" not in requested_lookup:
        requested_phase_names.append("liquid")
        requested_lookup.append("liquid")

    phase_frames: dict[str, list[Any]] = {p.lower(): [] for p in requested_phase_names}
    availability_rows: list[dict[str, Any]] = []
    pressure_rows: list[dict[str, Any]] = []
    extraction_report: dict[str, Any] = {
        "missing_phases": [],
        "skipped_samples": [],
        "failed_files": [],
        "interpolation_grid": interpolation_grid,
    }

    for _, mrow in work_df.iterrows():
        sample_label = str(mrow["sample_label"])
        excel_path = Path(str(mrow["excel_path"])).expanduser()

        if not excel_path.exists():
            extraction_report["failed_files"].append(
                {"sample_label": sample_label, "excel_path": str(excel_path), "error": "file_not_found"}
            )
            continue

        try:
            wb = openpyxl.load_workbook(excel_path, data_only=True, read_only=True)
        except Exception as exc:
            extraction_report["failed_files"].append(
                {"sample_label": sample_label, "excel_path": str(excel_path), "error": str(exc)}
            )
            continue
        try:
            sheet_name_map = {str(name).lower(): str(name) for name in wb.sheetnames}
            pa = _compute_pressure_analysis_from_workbook(wb, prefer_existing_sheet=True)
            pressure_rows.append(
                {
                    "sample_label": sample_label,
                    "source_excel": str(excel_path),
                    **pa,
                }
            )
            missing_for_sample = [p for p in requested_phase_names if p.lower() not in sheet_name_map]
            if missing_for_sample and missing_phase_policy == "error":
                raise RMeltsPipelineError(
                    f"Missing requested phases for {sample_label} in {excel_path.name}: {missing_for_sample}"
                )
            if missing_for_sample and missing_phase_policy == "skip_sample":
                extraction_report["skipped_samples"].append(
                    {
                        "sample_label": sample_label,
                        "excel_path": str(excel_path),
                        "reason": "missing_phase",
                        "missing_phases": missing_for_sample,
                    }
                )
                for p in requested_phase_names:
                    availability_rows.append(
                        {
                            "sample_label": sample_label,
                            "requested_phase": p.lower(),
                            "sheet_name": sheet_name_map.get(p.lower()),
                            "present": p.lower() in sheet_name_map,
                            "row_count": 0,
                            "status": "skipped_sample",
                        }
                    )
                continue

            for phase in requested_phase_names:
                phase_key = phase.lower()
                actual_sheet_name = sheet_name_map.get(phase_key)

                if actual_sheet_name is None:
                    extraction_report["missing_phases"].append(
                        {"sample_label": sample_label, "excel_path": str(excel_path), "phase": phase_key}
                    )
                    availability_rows.append(
                        {
                            "sample_label": sample_label,
                            "requested_phase": phase_key,
                            "sheet_name": None,
                            "present": False,
                            "row_count": 0,
                            "status": "missing",
                        }
                    )

                    if missing_phase_policy == "fill_nan":
                        placeholder_df = pd.DataFrame(
                            [_make_phase_placeholder_row(sample_label, str(excel_path), phase_key, None)]
                        )
                        phase_frames.setdefault(phase_key, []).append(placeholder_df)
                    continue

                raw_df = _sheet_to_dataframe(wb, actual_sheet_name)
                std_df = _standardize_sheet_columns(raw_df)
                std_df = _apply_temperature_pressure_filters(std_df, temperature_range, pressure_range)

                if std_df.empty and missing_phase_policy == "fill_nan":
                    std_df = pd.DataFrame(
                        [_make_phase_placeholder_row(sample_label, str(excel_path), phase_key, actual_sheet_name)]
                    )
                else:
                    std_df = std_df.copy()
                    std_df["sample_label"] = sample_label
                    std_df["source_excel"] = str(excel_path)
                    std_df["phase"] = phase_key
                    std_df["phase_sheet_name"] = actual_sheet_name

                # Guarantee core columns exist.
                core_defaults = {
                    "sample_label": sample_label,
                    "source_excel": str(excel_path),
                    "phase": phase_key,
                    "phase_sheet_name": actual_sheet_name,
                    "T_C": None,
                    "P_MPa": None,
                    "G_kJ": None,
                    "H_kJ": None,
                    "S_J_per_K": None,
                    "Cp_J_per_K": None,
                    "V_cm3": None,
                    "Mass_g": None,
                    "Density_g_cm3": None,
                    "Outcome": None,
                }
                for c, default_val in core_defaults.items():
                    if c not in std_df.columns:
                        std_df[c] = default_val

                if columns is not None:
                    selected_cols = [
                        "sample_label",
                        "source_excel",
                        "phase",
                        "phase_sheet_name",
                    ] + [str(c) for c in columns]
                    selected_cols = [c for c in dict.fromkeys(selected_cols) if c in std_df.columns]
                    std_df = std_df[selected_cols].copy()

                phase_frames.setdefault(phase_key, []).append(std_df)
                availability_rows.append(
                    {
                        "sample_label": sample_label,
                        "requested_phase": phase_key,
                        "sheet_name": actual_sheet_name,
                        "present": True,
                        "row_count": int(len(std_df)),
                        "status": "ok",
                    }
                )
        finally:
            try:
                wb.close()
            except Exception:
                pass

    phase_tables: dict[str, Any] = {}
    merged_parts: list[Any] = []
    for phase_key, frames in phase_frames.items():
        if frames:
            phase_df = pd.concat(frames, ignore_index=True, sort=False)
        else:
            phase_df = pd.DataFrame()
        phase_tables[phase_key] = phase_df
        if not phase_df.empty:
            merged_parts.append(phase_df.copy())

    merged_long_table = (
        pd.concat(merged_parts, ignore_index=True, sort=False) if merged_parts else pd.DataFrame()
    )
    phase_availability_table = pd.DataFrame(availability_rows)
    pressure_analysis_table = pd.DataFrame(pressure_rows)

    return GeobarometryBasisResult(
        phase_tables=phase_tables,
        merged_long_table=merged_long_table,
        phase_availability_table=phase_availability_table,
        pressure_analysis_table=pressure_analysis_table,
        extraction_report=extraction_report,
    )


def _extract_first_appearance_table_from_phase_tables(
    phase_tables: dict[str, Any],
    *,
    phases: Iterable[str] = ("quartz", "feldspar", "feldspar_1"),
    require_positive_mass: bool = True,
) -> Any:
    pd = _require_pandas()
    rows: list[Any] = []
    for phase in phases:
        key = str(phase).lower()
        df = phase_tables.get(key)
        if df is None or getattr(df, "empty", True):
            continue
        work = df.copy()
        if "T_C" not in work.columns or "P_MPa" not in work.columns:
            continue
        work["T_C"] = pd.to_numeric(work["T_C"], errors="coerce")
        work["P_MPa"] = pd.to_numeric(work["P_MPa"], errors="coerce")
        if require_positive_mass and "Mass_g" in work.columns:
            work["Mass_g"] = pd.to_numeric(work["Mass_g"], errors="coerce")
            work = work[work["Mass_g"] > 0]
        work = work.dropna(subset=["T_C", "P_MPa"])
        if work.empty:
            continue
        grouped = work.groupby("P_MPa", as_index=False)["T_C"].max()
        grouped = grouped.rename(columns={"T_C": "T_first_appearance_C"})
        grouped["phase"] = key
        rows.append(grouped[["phase", "P_MPa", "T_first_appearance_C"]].copy())
    if not rows:
        return pd.DataFrame(columns=["phase", "P_MPa", "T_first_appearance_C"])
    out = pd.concat(rows, ignore_index=True, sort=False)
    return out.sort_values(["phase", "P_MPa"], ascending=[True, False]).reset_index(drop=True)


def _choose_staged_temperature_window(
    first_appearance_df: Any,
    *,
    full_T1: float,
    full_T2: float,
    margin_high_C: float,
    margin_low_C: float,
) -> dict[str, Any]:
    pd = _require_pandas()
    out = {
        "reason": None,
        "full_T1": float(full_T1),
        "full_T2": float(full_T2),
        "T1_fine": float(full_T1),
        "T2_fine": float(full_T2),
        "tmin_first_appearance": None,
        "tmax_first_appearance": None,
    }
    if first_appearance_df is None or getattr(first_appearance_df, "empty", True):
        out["reason"] = "no_phase_appearance_rows"
        return out

    work = first_appearance_df.copy()
    if "T_first_appearance_C" not in work.columns:
        out["reason"] = "missing_T_first_appearance_C"
        return out
    work["T_first_appearance_C"] = pd.to_numeric(work["T_first_appearance_C"], errors="coerce")
    vals = work["T_first_appearance_C"].dropna().tolist()
    if not vals:
        out["reason"] = "no_numeric_phase_appearance_temps"
        return out

    tmin = float(min(vals))
    tmax = float(max(vals))
    T1_fine = min(float(full_T1), float(math.ceil(tmax + float(margin_high_C))))
    T2_fine = max(float(full_T2), float(math.floor(tmin - float(margin_low_C))))
    if T1_fine <= T2_fine:
        out["reason"] = "invalid_narrowed_window_fallback_to_full"
        out["tmin_first_appearance"] = tmin
        out["tmax_first_appearance"] = tmax
        return out

    out.update(
        {
            "reason": "from_first_appearance",
            "T1_fine": T1_fine,
            "T2_fine": T2_fine,
            "tmin_first_appearance": tmin,
            "tmax_first_appearance": tmax,
        }
    )
    return out


def _phase_success_flags_from_basis(basis: GeobarometryBasisResult) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    for phase in ["system", "liquid", "quartz", "feldspar", "feldspar_1"]:
        df = basis.phase_tables.get(phase)
        present = bool(df is not None and not getattr(df, "empty", True))
        flags[f"{phase}_sheet_nonempty"] = present
        if present and "Mass_g" in df.columns and "T_C" in df.columns and "P_MPa" in df.columns:
            work = df.copy()
            work["Mass_g"] = _require_pandas().to_numeric(work["Mass_g"], errors="coerce")
            valid = work[
                work["Mass_g"].fillna(0) > 0
            ]
            valid = valid.dropna(subset=["T_C", "P_MPa"])
            flags[f"{phase}_rows_nonzero_mass"] = int(len(valid))
        elif present and "T_C" in df.columns and "P_MPa" in df.columns:
            valid = df.dropna(subset=["T_C", "P_MPa"])
            flags[f"{phase}_rows_nonzero_mass"] = int(len(valid))
        else:
            flags[f"{phase}_rows_nonzero_mass"] = 0

    flags["melts_phase_success_qz_fsp_fsp1"] = bool(
        flags.get("system_sheet_nonempty")
        and flags.get("liquid_sheet_nonempty")
        and flags.get("feldspar_sheet_nonempty")
        and flags.get("feldspar_1_sheet_nonempty")
        and int(flags.get("feldspar_rows_nonzero_mass", 0)) > 0
        and int(flags.get("feldspar_1_rows_nonzero_mass", 0)) > 0
    )
    return flags


def _runtime_summary_from_manifest(manifest_df: Any) -> Any:
    pd = _require_pandas()
    if manifest_df is None or getattr(manifest_df, "empty", True):
        return pd.DataFrame()
    cols = [
        "sample_label",
        "status",
        "num_pressure_steps",
        "num_data_points",
        "melts_calc_time_s",
        "pressure_calc_time_s",
        "workbook_build_time_s",
        "total_time_s",
        "P_2phase_qtz_fsp_MPa",
        "P_3phase_qtz_fsp_fsp1_MPa",
        "pressure_calc_method",
        "pressure_calc_error",
    ]
    keep = [c for c in cols if c in manifest_df.columns]
    out = manifest_df[keep].copy()
    if {"melts_calc_time_s", "pressure_calc_time_s", "total_time_s"}.issubset(out.columns):
        for c in ["melts_calc_time_s", "pressure_calc_time_s", "workbook_build_time_s", "total_time_s"]:
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _write_pipeline_summary_workbook(
    *,
    workbook_path: Path,
    manifest_df: Any,
    basis: GeobarometryBasisResult,
    run_notes_rows: list[dict[str, Any]],
    extra_sheets: Optional[dict[str, Any]] = None,
) -> str:
    pd = _require_pandas()
    workbook_path = Path(workbook_path).expanduser().resolve()
    _ensure_dir(workbook_path.parent)

    runtime_summary_df = _runtime_summary_from_manifest(manifest_df)
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        manifest_df.to_excel(writer, sheet_name="manifest", index=False)
        if not runtime_summary_df.empty:
            runtime_summary_df.to_excel(writer, sheet_name="runtime_summary", index=False)
        if basis.pressure_analysis_table is not None and not basis.pressure_analysis_table.empty:
            basis.pressure_analysis_table.to_excel(writer, sheet_name="pressure_analysis", index=False)
        if basis.phase_availability_table is not None and not basis.phase_availability_table.empty:
            basis.phase_availability_table.to_excel(writer, sheet_name="phase_availability", index=False)
        for phase in ["system", "liquid", "quartz", "feldspar", "feldspar_1"]:
            df = basis.phase_tables.get(phase)
            if df is not None and not getattr(df, "empty", True):
                df.to_excel(writer, sheet_name=phase[:31], index=False)
        if run_notes_rows:
            pd.DataFrame(run_notes_rows).to_excel(writer, sheet_name="run_notes", index=False)
        for sheet_name, frame in (extra_sheets or {}).items():
            if frame is None or getattr(frame, "empty", False):
                if hasattr(frame, "empty") and frame.empty:
                    continue
            if frame is None:
                continue
            if not hasattr(frame, "to_excel"):
                frame = pd.DataFrame(frame)
            frame.to_excel(writer, sheet_name=str(sheet_name)[:31], index=False)
    return str(workbook_path)


def rMELTS_run_staged_first_appearance(
    prepared_mc_csv_path,
    *,
    output_dir,
    dataset_name,
    sample_label,
    scout_T1=1100,
    scout_T2=600,
    scout_dT=10,
    fine_dT=1,
    P1=400,
    P2=200,
    dP=25,
    fO2_constraint="TRUE",
    fO2_buffer="NNO",
    fO2_offset=0.0,
    max_composition_workers=1,
    max_pressure_workers=6,
    scout_margin_high_C=30,
    scout_margin_low_C=80,
):
    """
    Run a staged (scout then narrowed fine) MELTS workflow for a single prepared sample.
    """
    pd = _require_pandas()
    dataset_name = _sanitize_dataset_name(str(dataset_name))
    output_dir = Path(output_dir).expanduser().resolve()
    prepared_mc_csv_path = Path(prepared_mc_csv_path).expanduser().resolve()
    sample_label = str(sample_label)

    prepared_df = pd.read_csv(prepared_mc_csv_path)
    prepared_df = _normalize_prepared_dataframe_columns(prepared_df)
    work_df = prepared_df[prepared_df["sample_label"].astype(str) == sample_label].copy()
    if work_df.empty:
        raise RMeltsPipelineError(
            f"sample_label={sample_label!r} not found in prepared MC CSV: {prepared_mc_csv_path}"
        )
    if len(work_df) > 1:
        work_df = work_df.iloc[[0]].copy()

    staged_input_dir = _ensure_dir(output_dir / dataset_name / "staged_inputs")
    single_prepared_path = staged_input_dir / f"{sample_label}_prepared_single.csv"
    work_df.to_csv(single_prepared_path, index=False)

    scout_dataset_name = f"{dataset_name}_scout"
    scout_run = rMELTS_run(
        single_prepared_path,
        output_dir=output_dir,
        dataset_name=scout_dataset_name,
        T1=scout_T1,
        T2=scout_T2,
        dT=scout_dT,
        P1=P1,
        P2=P2,
        dP=dP,
        fO2_constraint=fO2_constraint,
        fO2_buffer=fO2_buffer,
        fO2_offset=fO2_offset,
        max_composition_workers=max_composition_workers,
        max_pressure_workers=max_pressure_workers,
        verbose=False,
    )
    scout_manifest_df = pd.read_csv(scout_run.manifest_csv_path)
    scout_basis = rMELTS_geobarometry_basis(
        scout_run.manifest_csv_path,
        phases=["quartz", "feldspar", "feldspar_1"],
        include_system=True,
        include_liquid=True,
        missing_phase_policy="skip_point",
    )
    scout_first = _extract_first_appearance_table_from_phase_tables(scout_basis.phase_tables)
    fine_window = _choose_staged_temperature_window(
        scout_first,
        full_T1=float(scout_T1),
        full_T2=float(scout_T2),
        margin_high_C=float(scout_margin_high_C),
        margin_low_C=float(scout_margin_low_C),
    )

    T1_fine = float(fine_window["T1_fine"])
    T2_fine = float(fine_window["T2_fine"])
    fine_dataset_name = f"{dataset_name}_fine"
    fine_run = rMELTS_run(
        single_prepared_path,
        output_dir=output_dir,
        dataset_name=fine_dataset_name,
        T1=T1_fine,
        T2=T2_fine,
        dT=fine_dT,
        P1=P1,
        P2=P2,
        dP=dP,
        fO2_constraint=fO2_constraint,
        fO2_buffer=fO2_buffer,
        fO2_offset=fO2_offset,
        max_composition_workers=max_composition_workers,
        max_pressure_workers=max_pressure_workers,
        verbose=False,
    )
    fine_manifest_df = pd.read_csv(fine_run.manifest_csv_path)
    fine_basis = rMELTS_geobarometry_basis(
        fine_run.manifest_csv_path,
        phases=["quartz", "feldspar", "feldspar_1"],
        include_system=True,
        include_liquid=True,
        missing_phase_policy="skip_point",
    )
    fine_first = _extract_first_appearance_table_from_phase_tables(fine_basis.phase_tables)

    scout_run_dir = Path(scout_run.run_dir)
    fine_run_dir = Path(fine_run.run_dir)
    suffix_token = ""
    for token in reversed(dataset_name.split("_")):
        if token.lower().startswith("suffix"):
            suffix_token = f"_{token}"
            break
    scout_summary_path = scout_run_dir / f"{sample_label}_scout_dT{int(scout_dT)}_pipeline_tables_with_pressure{suffix_token}.xlsx"
    fine_summary_path = fine_run_dir / f"{sample_label}_fine_dT{int(fine_dT)}_narrowed_pipeline_tables_with_pressure{suffix_token}.xlsx"

    scout_notes = [
        {
            "stage": "scout",
            "sample_label": sample_label,
            "prepared_mc_csv_path": str(single_prepared_path),
            "scout_T1": float(scout_T1),
            "scout_T2": float(scout_T2),
            "scout_dT": float(scout_dT),
            "P1": float(P1),
            "P2": float(P2),
            "dP": float(dP),
            "fO2_constraint": str(fO2_constraint),
            "fO2_buffer": str(fO2_buffer),
            "fO2_offset": float(fO2_offset),
            **_phase_success_flags_from_basis(scout_basis),
        }
    ]
    fine_notes = [
        {
            "stage": "fine",
            "sample_label": sample_label,
            "prepared_mc_csv_path": str(single_prepared_path),
            "scout_T1": float(scout_T1),
            "scout_T2": float(scout_T2),
            "scout_dT": float(scout_dT),
            "fine_T1": T1_fine,
            "fine_T2": T2_fine,
            "fine_dT": float(fine_dT),
            "P1": float(P1),
            "P2": float(P2),
            "dP": float(dP),
            "window_reason": fine_window.get("reason"),
            "window_tmin_first_appearance": fine_window.get("tmin_first_appearance"),
            "window_tmax_first_appearance": fine_window.get("tmax_first_appearance"),
            **_phase_success_flags_from_basis(fine_basis),
        }
    ]
    scout_summary_written = _write_pipeline_summary_workbook(
        workbook_path=scout_summary_path,
        manifest_df=scout_manifest_df,
        basis=scout_basis,
        run_notes_rows=scout_notes,
        extra_sheets={"scout_first_appearance": scout_first},
    )
    fine_summary_written = _write_pipeline_summary_workbook(
        workbook_path=fine_summary_path,
        manifest_df=fine_manifest_df,
        basis=fine_basis,
        run_notes_rows=fine_notes,
        extra_sheets={
            "scout_first_appearance": scout_first,
            "fine_first_appearance": fine_first,
            "fine_window": _require_pandas().DataFrame([fine_window]),
        },
    )

    return StagedRunResult(
        scout_run_result=scout_run,
        fine_run_result=fine_run,
        scout_first_appearance_table=scout_first,
        fine_first_appearance_table=fine_first,
        fine_window_metadata=fine_window,
        scout_summary_workbook_path=scout_summary_written,
        fine_summary_workbook_path=fine_summary_written,
    )


def _result_to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"Unsupported result object type: {type(obj)}")


def _write_example_manifest_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("rows must be non-empty")
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


__all__ = [
    "MELTSRunParams",
    "ConversionResult",
    "ApliteNormalizationResult",
    "RunResult",
    "StagedRunResult",
    "GeobarometryBasisResult",
    "MC_to_csv_rMELTS",
    "normalize_aplite_xrf_to_rowwise_mc",
    "rMELTS_run",
    "rMELTS_run_staged_first_appearance",
    "rMELTS_geobarometry_basis",
    "MELTS_REQUIRED_15_ROWS",
    "MELTS_ALL_ROWS",
    "_build_melts_input_wide_dataframe",
]
