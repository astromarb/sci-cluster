from __future__ import annotations

import contextlib
import csv
import importlib.util
import importlib
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


@dataclass
class MeltsExcelTemplateWriteResult:
    output_workbook_path: str
    template_workbook_path: str
    sample_label: Optional[str]
    target_column_header: str
    target_column_index: int
    multiple_comp_oxide_row_order: list[str]
    multiple_comp_oxide_order_matches_expected: bool
    input_sheet_oxide_row_order: Optional[list[str]] = None
    input_sheet_oxide_order_matches_expected: Optional[bool] = None
    rows_written_multiple_comp: int = 0
    rows_written_input_sheet: int = 0
    rows_written_sequences: int = 0
    settings_written: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _HelperPatchProfile:
    name: str
    fix_liquidus_timeout_loop: bool = False
    reset_liquidus_solver_after_none: bool = False
    raw_liquidus_execute_with_reset: bool = False
    fresh_liquidus_solver_per_iteration: bool = False
    fresh_liquidus_solver_per_branch: bool = False
    reset_solver_after_liquidus_presearch: bool = False
    wrap_main_execute_with_timeout: bool = False
    safe_execute_reraise_non_timeout: bool = False
    main_execute_timeout_s: float = 10.0
    skip_wet_liquidus_presearch: bool = False
    timeout_path_advances_temperature: bool = False
    reset_main_solver_after_timeout: bool = False
    bound_main_loop_failures: bool = False


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
    "profile_g_production_liquidus_reset": _HelperPatchProfile(
        name="profile_g_production_liquidus_reset",
        fix_liquidus_timeout_loop=True,
        reset_liquidus_solver_after_none=True,
    ),
    "profile_h_production_liquidus_raw_reset": _HelperPatchProfile(
        name="profile_h_production_liquidus_raw_reset",
        fix_liquidus_timeout_loop=True,
        reset_liquidus_solver_after_none=True,
        raw_liquidus_execute_with_reset=True,
    ),
    "profile_i_prototype_mainloop_bounded_reset": _HelperPatchProfile(
        name="profile_i_prototype_mainloop_bounded_reset",
        fix_liquidus_timeout_loop=True,
        reset_liquidus_solver_after_none=True,
        raw_liquidus_execute_with_reset=True,
        reset_solver_after_liquidus_presearch=True,
        wrap_main_execute_with_timeout=True,
        timeout_path_advances_temperature=True,
        reset_main_solver_after_timeout=True,
        bound_main_loop_failures=True,
    ),
    "profile_j_liquidus_fresh_iteration_bounded_main": _HelperPatchProfile(
        name="profile_j_liquidus_fresh_iteration_bounded_main",
        fix_liquidus_timeout_loop=True,
        reset_liquidus_solver_after_none=True,
        raw_liquidus_execute_with_reset=True,
        fresh_liquidus_solver_per_iteration=True,
        reset_solver_after_liquidus_presearch=True,
        wrap_main_execute_with_timeout=True,
        timeout_path_advances_temperature=True,
        reset_main_solver_after_timeout=True,
        bound_main_loop_failures=True,
    ),
    "profile_k_liquidus_fresh_branch_bounded_main": _HelperPatchProfile(
        name="profile_k_liquidus_fresh_branch_bounded_main",
        fix_liquidus_timeout_loop=True,
        reset_liquidus_solver_after_none=True,
        raw_liquidus_execute_with_reset=True,
        fresh_liquidus_solver_per_branch=True,
        reset_solver_after_liquidus_presearch=True,
        wrap_main_execute_with_timeout=True,
        timeout_path_advances_temperature=True,
        reset_main_solver_after_timeout=True,
        bound_main_loop_failures=True,
    ),
    "profile_l_main_timeout_wrapper_reraise_non_timeout": _HelperPatchProfile(
        name="profile_l_main_timeout_wrapper_reraise_non_timeout",
        fix_liquidus_timeout_loop=True,
        reset_liquidus_solver_after_none=True,
        raw_liquidus_execute_with_reset=True,
        fresh_liquidus_solver_per_branch=True,
        reset_solver_after_liquidus_presearch=True,
        wrap_main_execute_with_timeout=True,
        safe_execute_reraise_non_timeout=True,
        timeout_path_advances_temperature=True,
        reset_main_solver_after_timeout=True,
        bound_main_loop_failures=True,
    ),
}

_DEFAULT_HELPER_PATCH_PROFILE_NAME = "profile_h_production_liquidus_raw_reset"

_HELPER_IMPLEMENTATION_ENV_VAR = "RMELTS_INTERNAL_HELPER_IMPLEMENTATION"


@dataclass(frozen=True)
class _HelperImplementationSpec:
    name: str
    source_path: Optional[Path] = None
    patch_profile: Optional[Any] = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _repo_local_liam_clone_helper_path() -> Path:
    return _repo_root() / "sci_helpers" / "rmelts_liam_clone_helper.py"


def _resolve_helper_implementation_spec(implementation: Optional[str] = None) -> _HelperImplementationSpec:
    impl = implementation
    if impl is None:
        impl = os.environ.get(_HELPER_IMPLEMENTATION_ENV_VAR, "").strip() or None
    if impl is None:
        return _HelperImplementationSpec(name="default")
    key = str(impl).strip().lower()
    if key == "patched_profile_k":
        return _HelperImplementationSpec(
            name="patched_profile_k",
            source_path=None,
            patch_profile="profile_k_liquidus_fresh_branch_bounded_main",
        )
    if key == "liam_clone":
        clone_path = _repo_local_liam_clone_helper_path()
        if not clone_path.exists():
            raise RMeltsPipelineError(f"Repo-local Liam clone helper not found: {clone_path}")
        # Keep Liam semantics but still inject the spawn bootstrap in the run-local copy.
        return _HelperImplementationSpec(
            name="liam_clone",
            source_path=clone_path,
            patch_profile="profile_a_unpatched",
        )
    if key == "liam_clone_min_safe":
        clone_path = _repo_local_liam_clone_helper_path()
        if not clone_path.exists():
            raise RMeltsPipelineError(f"Repo-local Liam clone helper not found: {clone_path}")
        # Liam-like semantics with minimal liquidus-side safety guards/recovery only.
        return _HelperImplementationSpec(
            name="liam_clone_min_safe",
            source_path=clone_path,
            patch_profile="profile_h_production_liquidus_raw_reset",
        )
    if key == "liam_clone_guarded_main":
        clone_path = _repo_local_liam_clone_helper_path()
        if not clone_path.exists():
            raise RMeltsPipelineError(f"Repo-local Liam clone helper not found: {clone_path}")
        # Liam-like semantics with liquidus safety plus bounded main-loop recovery.
        return _HelperImplementationSpec(
            name="liam_clone_guarded_main",
            source_path=clone_path,
            patch_profile="profile_i_prototype_mainloop_bounded_reset",
        )
    raise RMeltsPipelineError(
        f"Unknown helper implementation '{impl}'. Expected one of: patched_profile_k, liam_clone, liam_clone_min_safe, liam_clone_guarded_main"
    )


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


_THERMOENGINE_REDOX_DB_CACHE: Any = None


def _require_thermoengine_model() -> Any:
    try:
        return importlib.import_module("thermoengine.model")
    except ImportError as exc:
        raise RMeltsPipelineError(
            "Required dependency 'thermoengine' is not installed in this Python environment."
        ) from exc


def _get_thermoengine_redox_db() -> Any:
    global _THERMOENGINE_REDOX_DB_CACHE
    if _THERMOENGINE_REDOX_DB_CACHE is None:
        model_mod = _require_thermoengine_model()
        _THERMOENGINE_REDOX_DB_CACHE = model_mod.Database()
    return _THERMOENGINE_REDOX_DB_CACHE


def _scalarize_numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if hasattr(value, "shape"):
        try:
            shape = tuple(value.shape)
        except Exception:
            shape = ()
        if shape not in ((),):
            try:
                import numpy as _np  # local import to avoid hard dependency at import-time
                arr = _np.asarray(value).reshape(-1)
                if arr.size == 0:
                    return None
                value = arr[0].item() if hasattr(arr[0], "item") else arr[0]
            except Exception:
                try:
                    value = list(value)[0]
                except Exception:
                    return None
    return _safe_float(value)


def _try_import_psutil() -> Optional[Any]:
    try:
        return __import__("psutil")
    except Exception:
        return None


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


def _normalize_redox_buffer_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    key = s.lower()
    alias = {
        "nno": "NNO",
        "qfm": "QFM",
        "fmq": "FMQ",
        "iw": "IW",
        "hm": "HM",
        "mh": "MH",
        "mw": "MW",
        "wm": "WM",
        "qif": "QIF",
        "cco": "CCO",
        "mmo": "MMO",
    }
    return alias.get(key, s.upper())


def _redox_buffer_logfO2(T_K: float, P_bar: float, buffer_name: str) -> Optional[float]:
    db = _get_thermoengine_redox_db()
    raw = db.redox_buffer(float(T_K), float(P_bar), buffer=str(buffer_name))
    return _scalarize_numeric(raw)


def _extract_init_cond_redox_settings(wb: Any) -> dict[str, Any]:
    out = {
        "fO2_buffer": None,
        "fO2_offset": 0.0,
        "fO2_value_row_present": False,
        "fO2_buffer_row_present": False,
    }
    sheet_name = None
    for s in wb.sheetnames:
        if str(s).strip().lower() == "init_cond":
            sheet_name = s
            break
    if sheet_name is None:
        return out
    ws = wb[sheet_name]
    max_row = min(int(getattr(ws, "max_row", 0) or 0), 80)
    max_col = min(int(getattr(ws, "max_column", 0) or 0), 20)
    label_to_value: dict[str, Any] = {}
    for r in range(1, max_row + 1):
        row_vals = [ws.cell(row=r, column=c).value for c in range(1, max_col + 1)]
        norm_vals = [str(v).strip().lower() if isinstance(v, str) else None for v in row_vals]
        for target in ("fO2 value", "fO2 buffer", "fo2 value", "fo2 buffer", "fo2path"):
            if target.lower() not in [nv for nv in norm_vals if nv is not None]:
                continue
            idx = next(i for i, nv in enumerate(norm_vals) if nv == target.lower())
            value = None
            for j in range(idx + 1, len(row_vals)):
                cand = row_vals[j]
                if cand is None:
                    continue
                if isinstance(cand, str) and cand.strip() == "":
                    continue
                value = cand
                break
            label_to_value[target.lower()] = value
    if "fo2 buffer" in label_to_value:
        out["fO2_buffer"] = _normalize_redox_buffer_name(label_to_value["fo2 buffer"])
        out["fO2_buffer_row_present"] = True
    elif "fo2path" in label_to_value:
        out["fO2_buffer"] = _normalize_redox_buffer_name(label_to_value["fo2path"])
        out["fO2_buffer_row_present"] = True
    if "fo2 value" in label_to_value:
        out["fO2_offset"] = _coerce_numeric(label_to_value["fo2 value"]) or 0.0
        out["fO2_value_row_present"] = True
    return out


def _sheet_header_col_map(ws: Any) -> dict[str, int]:
    mapping: dict[str, int] = {}
    max_col = int(getattr(ws, "max_column", 0) or 0)
    for c in range(1, max_col + 1):
        v = ws.cell(row=1, column=c).value
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        mapping[s.lower()] = c
    return mapping


def _populate_workbook_deltaqfm_columns(wb: Any) -> dict[str, Any]:
    summary = {
        "deltaqfm_enriched": False,
        "deltaqfm_rows_updated": 0,
        "deltaqfm_sheets_updated": 0,
        "deltaqfm_error": None,
        "deltaqfm_buffer": None,
        "deltaqfm_offset": None,
        "deltaqfm_skipped_reason": None,
        "deltaqfm_sheet_details": [],
    }
    redox = _extract_init_cond_redox_settings(wb)
    buffer_name = _normalize_redox_buffer_name(redox.get("fO2_buffer"))
    offset = _coerce_numeric(redox.get("fO2_offset"))
    if offset is None:
        offset = 0.0
    summary["deltaqfm_buffer"] = buffer_name
    summary["deltaqfm_offset"] = offset
    if buffer_name is None:
        summary["deltaqfm_skipped_reason"] = "No fO2 buffer found in init_cond"
        return summary

    # Normalize common alias before thermoengine call.
    if buffer_name == "FMQ":
        buffer_name = "QFM"

    total_rows = 0
    sheets_updated = 0
    for s in wb.sheetnames:
        s_lower = str(s).strip().lower()
        if s_lower in {"init_cond", "pressure analysis"}:
            continue
        ws = wb[s]
        headers = _sheet_header_col_map(ws)
        t_col = headers.get("t (c)")
        p_col = headers.get("p (mpa)")
        dqfm_col = headers.get("deltaqfm")
        if not (t_col and p_col and dqfm_col):
            continue
        rows_updated = 0
        max_row = int(getattr(ws, "max_row", 0) or 0)
        for r in range(2, max_row + 1):
            t_c = _coerce_numeric(ws.cell(row=r, column=t_col).value)
            p_mpa = _coerce_numeric(ws.cell(row=r, column=p_col).value)
            if t_c is None or p_mpa is None:
                continue
            T_K = float(t_c) + 273.15
            P_bar = float(p_mpa) * 10.0
            try:
                logf_buffer = _redox_buffer_logfO2(T_K, P_bar, str(buffer_name))
                logf_qfm = _redox_buffer_logfO2(T_K, P_bar, "QFM")
                if logf_buffer is None or logf_qfm is None:
                    continue
                delta_qfm = float(logf_buffer) + float(offset) - float(logf_qfm)
            except Exception as exc:
                summary["deltaqfm_error"] = str(exc)
                summary["deltaqfm_skipped_reason"] = "Redox buffer calculation failed"
                return summary
            ws.cell(row=r, column=dqfm_col, value=float(delta_qfm))
            rows_updated += 1
        if rows_updated > 0:
            sheets_updated += 1
            total_rows += rows_updated
            summary["deltaqfm_sheet_details"].append({"sheet": str(s), "rows_updated": rows_updated})
    summary["deltaqfm_rows_updated"] = total_rows
    summary["deltaqfm_sheets_updated"] = sheets_updated
    summary["deltaqfm_enriched"] = sheets_updated > 0
    if not summary["deltaqfm_enriched"] and summary["deltaqfm_skipped_reason"] is None:
        summary["deltaqfm_skipped_reason"] = "No sheets with T/P/deltaQFM headers found"
    return summary


def _enrich_deltaqfm_in_generated_workbooks(run_dir: Path) -> dict[str, Any]:
    openpyxl = _require_openpyxl()
    results = {
        "workbooks_seen": 0,
        "workbooks_updated": 0,
        "rows_updated": 0,
        "workbook_summaries": [],
        "errors": [],
    }
    for excel_path in sorted((run_dir / "parallel-results").glob("**/*.xlsx")):
        results["workbooks_seen"] += 1
        try:
            wb = openpyxl.load_workbook(excel_path)
            try:
                summary = _populate_workbook_deltaqfm_columns(wb)
                if summary.get("deltaqfm_enriched"):
                    wb.save(excel_path)
                    results["workbooks_updated"] += 1
                    results["rows_updated"] += int(summary.get("deltaqfm_rows_updated") or 0)
                summary["excel_path"] = str(excel_path)
                results["workbook_summaries"].append(summary)
            finally:
                try:
                    wb.close()
                except Exception:
                    pass
        except Exception as exc:
            results["errors"].append({"excel_path": str(excel_path), "error": str(exc)})
    return results


def _nan_to_zero(value: Any) -> float:
    f = _safe_float(value)
    return 0.0 if f is None else float(f)


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


def _feot_as_feo_from_fe_speciation(feo_wt: float, fe2o3_wt: float) -> float:
    """
    Convert FeO + Fe2O3 wt% to FeOt wt% expressed as FeO-equivalent.
    """
    return float(feo_wt) + float(fe2o3_wt) * ((2.0 * M_FEO) / M_FE2O3)


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


def _snapshot_descendant_processes() -> dict[str, Any]:
    """
    Capture the current process descendants (recursive) so we can clean up any
    MELTS worker processes left behind after helper execution.
    """
    snapshot: dict[str, Any] = {
        "parent_pid": os.getpid(),
        "psutil_available": False,
        "descendant_pids": [],
        "warnings": [],
    }
    psutil = _try_import_psutil()
    if psutil is None:
        snapshot["warnings"].append("psutil unavailable; descendant snapshot skipped")
        return snapshot
    snapshot["psutil_available"] = True
    try:
        parent = psutil.Process(int(snapshot["parent_pid"]))
        children = parent.children(recursive=True)
        snapshot["descendant_pids"] = sorted(
            [
                int(p.pid)
                for p in children
                if getattr(p, "pid", None) is not None and int(p.pid) != int(snapshot["parent_pid"])
            ]
        )
    except Exception as exc:
        snapshot["warnings"].append(f"descendant snapshot failed: {exc}")
    return snapshot


def _cleanup_descendant_processes(
    before_snapshot: Optional[dict[str, Any]],
    *,
    grace_timeout_s: float = 2.0,
) -> dict[str, Any]:
    """
    Best-effort cleanup of leftover descendants created during a helper run.

    This is intentionally scoped to descendants of the current pipeline process
    to avoid killing unrelated Python jobs (PyCharm, notebooks, etc.).
    """
    t0 = time.time()
    summary: dict[str, Any] = {
        "cleanup_attempted": True,
        "cleanup_parent_pid": os.getpid(),
        "cleanup_psutil_available": False,
        "cleanup_descendants_before_count": 0,
        "cleanup_descendants_after_count": 0,
        "cleanup_descendants_found": 0,
        "cleanup_target_pids": [],
        "cleanup_terminated_count": 0,
        "cleanup_killed_count": 0,
        "cleanup_remaining_count": 0,
        "cleanup_duration_s": 0.0,
        "cleanup_warnings": [],
    }
    try:
        before_pids = set()
        if isinstance(before_snapshot, dict):
            before_vals = before_snapshot.get("descendant_pids", [])
            if isinstance(before_vals, (list, tuple, set)):
                for pid in before_vals:
                    try:
                        before_pids.add(int(pid))
                    except Exception:
                        continue
            before_warnings = before_snapshot.get("warnings")
            if isinstance(before_warnings, list):
                for w in before_warnings:
                    summary["cleanup_warnings"].append(str(w))
        summary["cleanup_descendants_before_count"] = len(before_pids)

        psutil = _try_import_psutil()
        if psutil is None:
            summary["cleanup_warnings"].append("psutil unavailable; cleanup skipped")
            return summary
        summary["cleanup_psutil_available"] = True

        try:
            parent = psutil.Process(int(summary["cleanup_parent_pid"]))
            current_desc = [
                p
                for p in parent.children(recursive=True)
                if getattr(p, "pid", None) is not None and int(p.pid) != int(summary["cleanup_parent_pid"])
            ]
        except Exception as exc:
            summary["cleanup_warnings"].append(f"descendant enumeration failed: {exc}")
            return summary

        summary["cleanup_descendants_after_count"] = len(current_desc)
        targets = []
        for proc in current_desc:
            try:
                pid = int(proc.pid)
            except Exception:
                continue
            if pid in before_pids:
                continue
            targets.append(proc)
        summary["cleanup_descendants_found"] = len(targets)
        summary["cleanup_target_pids"] = sorted(
            [int(getattr(p, "pid")) for p in targets if getattr(p, "pid", None) is not None]
        )
        if not targets:
            return summary

        for proc in targets:
            try:
                proc.terminate()
            except Exception as exc:
                pid = getattr(proc, "pid", "unknown")
                summary["cleanup_warnings"].append(f"terminate failed pid={pid}: {exc}")
        try:
            gone, alive = psutil.wait_procs(targets, timeout=float(grace_timeout_s))
        except Exception as exc:
            summary["cleanup_warnings"].append(f"wait_procs after terminate failed: {exc}")
            gone, alive = [], targets
        summary["cleanup_terminated_count"] = len(gone)

        if alive:
            for proc in alive:
                try:
                    proc.kill()
                except Exception as exc:
                    pid = getattr(proc, "pid", "unknown")
                    summary["cleanup_warnings"].append(f"kill failed pid={pid}: {exc}")
            try:
                gone2, alive2 = psutil.wait_procs(alive, timeout=float(grace_timeout_s))
            except Exception as exc:
                summary["cleanup_warnings"].append(f"wait_procs after kill failed: {exc}")
                gone2, alive2 = [], alive
            summary["cleanup_killed_count"] = len(gone2)
            summary["cleanup_remaining_count"] = len(alive2)
            if alive2:
                alive_pids = [getattr(p, "pid", "unknown") for p in alive2]
                summary["cleanup_warnings"].append(f"processes still alive after kill: {alive_pids}")
    except Exception as exc:
        # Cleanup should never crash the run path.
        summary["cleanup_warnings"].append(f"unexpected cleanup error: {exc}")
    finally:
        summary["cleanup_duration_s"] = max(time.time() - t0, 0.0)
    return summary


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
        # Preserve empty cells / NaNs for missing optional oxides.
        ordered[oxide] = pd.to_numeric(ordered[oxide], errors="coerce")
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
    fe3fet=None,
    h2o_col="H2O",
    validate_only=True,
    total_tolerance_wt=0.5,
    oxide_alias_map=None,
    label_prefix="MC",
    write_qc_report=True,
):
    """
    Prepare a row-wise Monte Carlo CSV into a standardized composition CSV for rhyolite-MELTS input assembly.

    Thesis workflow assumptions (enforced):
    - Input is row-wise (one sample per row).
    - Compositions are preserved exactly as supplied (no renormalization in this function).
    - Missing MELTS oxide rows are left empty/NaN so MELTS can ignore them.
    - Total iron (FeOt) is only repartitioned to FeO + Fe2O3 when fe3fet is explicitly provided.
    """
    pd = _require_pandas()

    if validate_only is not True:
        raise ValueError(
            "Composition preservation is enforced for this workflow; renormalization is disabled. "
            "Use validate_only=True."
        )
    fe3fet_explicit = fe3fet is not None
    if fe3fet_explicit:
        if not (0.0 <= float(fe3fet) <= 1.0):
            raise ValueError("fe3fet must be between 0 and 1 inclusive when explicitly provided")
        if fe_total_basis != "FeOt_as_FeO":
            raise NotImplementedError(
                f"Unsupported fe_total_basis={fe_total_basis!r}. FeOt splitting supports 'FeOt_as_FeO'."
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

        # Start with direct oxides where available. Missing rows are preserved as NaN.
        for oxide in MELTS_OXIDE_ROWS:
            col = resolved_cols.get(oxide)
            val = _coerce_numeric(row[col]) if col is not None else None
            prepared[oxide] = math.nan if val is None else float(val)

        # Iron handling priority:
        # 1) If both FeO and Fe2O3 columns exist directly, keep them.
        # 2) Otherwise derive from total iron column if available.
        feo_direct = resolved_cols.get("FeO") is not None
        fe2o3_direct = resolved_cols.get("Fe2O3") is not None
        fe_split_applied = False
        fe_input_mode = "unknown"
        feot_original = None
        if feo_direct and fe2o3_direct:
            # Preserve user-provided FeO/Fe2O3 exactly.
            fe_input_mode = "direct_fe_speciation_preserved"
            if fe_total_col_resolved is not None:
                feot_original = _coerce_numeric(row[fe_total_col_resolved])
        elif feo_direct ^ fe2o3_direct:
            # Partial direct Fe speciation is ambiguous; do not overwrite with inferred values.
            fe_input_mode = "partial_direct_fe_speciation_error"
            if fe_total_col_resolved is not None:
                feot_original = _coerce_numeric(row[fe_total_col_resolved])
            missing_required.append("FeO_and_Fe2O3_both_required_if_direct_Fe_speciation_used")
            row_warnings.append(
                "Only one of FeO/Fe2O3 was supplied. Composition-preserving mode requires both, "
                "or FeOt with an explicit fe3fet."
            )
        elif fe_total_col_resolved is not None:
            feot_original = _coerce_numeric(row[fe_total_col_resolved])
            if feot_original is None:
                fe_input_mode = "feot_missing_or_non_numeric_error"
                missing_required.append("FeOt_numeric_or_explicit_FeO_Fe2O3")
                row_warnings.append(
                    f"{fe_total_col} missing/non-numeric and no direct FeO/Fe2O3 supplied"
                )
            elif fe3fet_explicit:
                feo_wt, fe2o3_wt = _fe_split_from_feot_as_feo(float(feot_original), float(fe3fet))
                prepared["FeO"] = feo_wt
                prepared["Fe2O3"] = fe2o3_wt
                fe_split_applied = True
                fe_input_mode = "feot_explicit_split"
            else:
                fe_input_mode = "feot_present_no_explicit_split_error"
                missing_required.append("FeO_and_Fe2O3_or_explicit_fe3fet_for_FeOt")
                row_warnings.append(
                    "FeOt supplied without FeO/Fe2O3. Composition-preserving mode does not repartition iron "
                    "unless fe3fet is explicitly provided."
                )
        else:
            fe_input_mode = "no_fe_inputs_error"
        # Validate essential inputs (FeO/Fe2O3 allowed if both zeros, but required presence is handled above).
        for required in REQUIRED_PRESENT_OR_DERIVABLE:
            col = resolved_cols.get(required)
            if col is None and required != "H2O":
                missing_required.append(required)
            elif required == "H2O" and resolved_cols.get("H2O") is None:
                missing_required.append("H2O")

        if resolved_cols.get("FeO") is None and resolved_cols.get("Fe2O3") is None and fe_total_col_resolved is None:
            missing_required.extend(["FeO_and_Fe2O3_or_FeOt"])

        # Track totals
        prepared_total = float(sum(_nan_to_zero(prepared.get(oxide, math.nan)) for oxide in MELTS_OXIDE_ROWS))
        feot_implied_from_prepared = _feot_as_feo_from_fe_speciation(
            _nan_to_zero(prepared.get("FeO", math.nan)),
            _nan_to_zero(prepared.get("Fe2O3", math.nan)),
        )
        feot_delta_vs_original = (
            None
            if feot_original is None
            else float(feot_implied_from_prepared) - float(feot_original)
        )
        total_warning = abs(prepared_total - 100.0) > float(total_tolerance_wt)
        if total_warning:
            row_warnings.append(
                f"Prepared oxide total {prepared_total:.4f} wt% outside tolerance ±{total_tolerance_wt} of 100"
            )

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
                "fe_input_mode": fe_input_mode,
                "fe3fet": None if fe3fet is None else float(fe3fet),
                "fe_split_applied": bool(fe_split_applied),
                "FeOt_original": feot_original,
                "FeOt_implied_from_prepared_as_FeO": feot_implied_from_prepared,
                "FeOt_delta_vs_original_as_FeO": feot_delta_vs_original,
                "FeO_prepared": prepared.get("FeO", math.nan),
                "Fe2O3_prepared": prepared.get("Fe2O3", math.nan),
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
        "composition_preserved": True,
        "fe_split_requires_explicit_fe3fet": True,
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
    Convert a column-wise aplite XRF table into a row-wise MC-style CSV while preserving source oxide values.

    Expected source layout (e.g., Aplites_HAL_XRF_noUncertainty.csv):
    - First column = oxide names (Sample Name)
    - Remaining columns = sample compositions

    Notes for thesis reproducibility:
    - This helper preserves the source oxide values exactly (no dry renormalization).
    - H2O is appended explicitly as a separate user-chosen value.
    - The legacy `dry_normalize_to` parameter is retained for API compatibility but is not applied.
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
    if not (rowwise["dry_total_original"] > 0).any():
        raise RMeltsPipelineError("All dry totals are <= 0 after transposing aplite XRF table")
    rowwise["dry_total_preserved"] = rowwise["dry_total_original"]
    rowwise["H2O"] = float(fixed_h2o_wt)

    sample_labels = rowwise["SampleID"].astype(str).tolist()
    all_name = f"{xrf_csv_path.stem}_rowwise_preserved_H2O{str(fixed_h2o_wt).replace('.', 'p')}.csv"
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
            target_name = f"{target_df.iloc[0]['SampleID']}_preserved_one_row.csv"
        else:
            target_name = f"{dataset_name}_targets_preserved_rowwise.csv"
        target_path = norm_dir / target_name
        target_df.to_csv(target_path, index=False)

    metadata = {
        "xrf_csv_path": str(xrf_csv_path),
        "sample_name_col": str(sample_name_col),
        "required_rows": [str(r) for r in required_rows],
        "fe_total_row": str(fe_total_row),
        "fixed_h2o_wt": float(fixed_h2o_wt),
        "composition_preserved": True,
        "dry_normalize_to_requested": float(dry_normalize_to),
        "dry_normalization_applied": False,
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


def _extract_single_prepared_composition_row(
    *,
    composition: Optional[dict[str, Any]] = None,
    prepared_mc_csv_path: Optional[Any] = None,
    sample_label: Optional[str] = None,
) -> tuple[dict[str, Any], Optional[str]]:
    pd = _require_pandas()
    if composition is None and prepared_mc_csv_path is None:
        raise ValueError("Provide either composition or prepared_mc_csv_path")
    if composition is not None and prepared_mc_csv_path is not None:
        raise ValueError("Provide only one of composition or prepared_mc_csv_path")

    if composition is not None:
        comp = dict(composition)
        resolved_label = comp.get("sample_label")
        if resolved_label is not None:
            resolved_label = str(resolved_label)
        if sample_label is not None:
            resolved_label = str(sample_label)
        row: dict[str, Any] = {"sample_label": resolved_label or "sample"}
        for oxide in MELTS_OXIDE_ROWS:
            row[oxide] = _coerce_numeric(comp.get(oxide))
        return row, row["sample_label"]

    df = pd.read_csv(prepared_mc_csv_path)
    df = _normalize_prepared_dataframe_columns(df)
    if sample_label is None:
        if len(df) != 1:
            raise RMeltsPipelineError(
                "prepared_mc_csv_path contains multiple samples; provide sample_label explicitly"
            )
        rec = df.iloc[0]
    else:
        mask = df["sample_label"].astype(str) == str(sample_label)
        if int(mask.sum()) == 0:
            raise RMeltsPipelineError(f"sample_label '{sample_label}' not found in prepared_mc_csv_path")
        rec = df.loc[mask].iloc[0]
    row = {"sample_label": str(rec["sample_label"])}
    for oxide in MELTS_OXIDE_ROWS:
        row[oxide] = _coerce_numeric(rec[oxide])
    return row, row["sample_label"]


def _excel_defined_name_first_cell(wb: Any, name: str) -> Optional[tuple[Any, int, int]]:
    openpyxl = _require_openpyxl()
    try:
        dn = wb.defined_names[name]
    except Exception:
        return None
    try:
        destinations = list(dn.destinations)
    except Exception:
        return None
    if not destinations:
        return None
    sheet_name, ref = destinations[0]
    if sheet_name not in wb.sheetnames:
        return None
    try:
        min_col, min_row, _max_col, _max_row = openpyxl.utils.range_boundaries(ref)
    except Exception:
        return None
    return wb[sheet_name], int(min_row), int(min_col)


def _worksheet_label_row_map(
    ws: Any,
    *,
    label_col: int = 1,
    row_min: int = 1,
    row_max: Optional[int] = None,
) -> dict[str, int]:
    max_row = int(getattr(ws, "max_row", 0) or 0)
    if row_max is None:
        row_max = max_row
    mapping: dict[str, int] = {}
    for r in range(int(row_min), int(row_max) + 1):
        v = ws.cell(row=r, column=int(label_col)).value
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        mapping[s.lower()] = r
    return mapping


def _worksheet_oxide_row_order(
    ws: Any,
    *,
    label_col: int = 1,
    row_min: int = 1,
    row_max: Optional[int] = None,
) -> list[str]:
    max_row = int(getattr(ws, "max_row", 0) or 0)
    if row_max is None:
        row_max = max_row
    rows: list[str] = []
    oxide_set = {o.lower() for o in MELTS_OXIDE_ROWS}
    for r in range(int(row_min), int(row_max) + 1):
        v = ws.cell(row=r, column=int(label_col)).value
        if v is None:
            continue
        s = str(v).strip()
        if s.lower() in oxide_set:
            rows.append(s)
    return rows


def _coerce_excel_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in {"true", "t", "yes", "y", "1"}:
            return True
        if key in {"false", "f", "no", "n", "0"}:
            return False
    return value


def _set_excel_cell_preserving_blank(cell: Any, value: Any) -> None:
    if value is None:
        cell.value = None
        return
    if isinstance(value, float) and math.isnan(value):
        cell.value = None
        return
    cell.value = value


def _multiple_comp_column_index(ws: Any, target_column_header: str) -> int:
    target_norm = str(target_column_header).strip().lower()
    max_col = int(getattr(ws, "max_column", 0) or 0)
    for c in range(1, max_col + 1):
        v = ws.cell(row=1, column=c).value
        if v is None:
            continue
        if str(v).strip().lower() == target_norm:
            return c
    new_col = max_col + 1 if max_col > 0 else 2
    ws.cell(row=1, column=new_col, value=str(target_column_header))
    return new_col


def _write_multiple_comp_column(
    wb: Any,
    *,
    column_header: str,
    composition_row: dict[str, Any],
    params: MELTSRunParams,
    starting_T: Any,
    min_liq_content: Any,
    fractionate: Any,
    phase_1: Any,
    phase_2: Any,
    phase_3: Any,
    formula: Any,
    delta_h: Any,
    delta_v: Any,
    delta_s: Any,
) -> dict[str, Any]:
    if "Multiple_Comp" not in wb.sheetnames:
        raise RMeltsPipelineError("Workbook template missing 'Multiple_Comp' sheet")
    ws = wb["Multiple_Comp"]
    col = _multiple_comp_column_index(ws, column_header)
    row_map = _worksheet_label_row_map(ws, row_min=1, row_max=120)
    oxide_order = _worksheet_oxide_row_order(ws, row_min=1, row_max=40)

    rows_written = 0
    for oxide in MELTS_OXIDE_ROWS:
        r = row_map.get(oxide.lower())
        if r is None:
            continue
        _set_excel_cell_preserving_blank(ws.cell(row=r, column=col), composition_row.get(oxide))
        rows_written += 1

    calc_settings = {
        "Model": params.model,
        "Calculation": params.calculation,
        "T1": float(params.T1),
        "T2": float(params.T2),
        "ΔT": float(params.dT),
        "T unit": params.T_unit,
        "P1": float(params.P1),
        "P2": float(params.P2),
        "ΔP": float(params.dP),
        "P unit": params.P_unit,
        "fO2 offset": float(params.fO2_offset),
        "fO2 buffer": _normalize_redox_buffer_name(params.fO2_buffer) or params.fO2_buffer,
        "fO2 constraint": _coerce_excel_bool(params.fO2_constraint),
        "Starting T": starting_T,
        "Min liq content": min_liq_content,
        "Fractionate": fractionate,
        "Phase 1": phase_1,
        "Phase 2": phase_2,
        "Phase 3": phase_3,
        "Formula": formula,
        "ΔH": delta_h,
        "ΔV": delta_v,
        "ΔS": delta_s,
    }
    for label, value in calc_settings.items():
        r = row_map.get(str(label).strip().lower())
        if r is None:
            continue
        _set_excel_cell_preserving_blank(ws.cell(row=r, column=col), value)
        rows_written += 1

    return {
        "target_column_header": str(column_header),
        "target_column_index": int(col),
        "rows_written": int(rows_written),
        "oxide_row_order": oxide_order,
        "oxide_order_matches_expected": oxide_order[: len(MELTS_OXIDE_ROWS)] == MELTS_OXIDE_ROWS,
    }


def _write_input_and_sequences_sheet_snapshot(
    wb: Any,
    *,
    composition_row: dict[str, Any],
    params: MELTSRunParams,
    starting_T: Any,
    min_liq_content: Any,
    fractionate: Any,
    phase_1: Any,
    phase_2: Any,
    phase_3: Any,
    formula: Any,
    delta_h: Any,
    delta_v: Any,
    delta_s: Any,
) -> dict[str, Any]:
    rows_written_input = 0
    rows_written_sequences = 0
    input_order: Optional[list[str]] = None
    input_matches: Optional[bool] = None

    if "Input" in wb.sheetnames:
        ws_in = wb["Input"]
        row_map_in = _worksheet_label_row_map(ws_in, row_min=1, row_max=120)
        input_order = _worksheet_oxide_row_order(ws_in, row_min=1, row_max=40)
        input_matches = input_order[: len(MELTS_OXIDE_ROWS)] == MELTS_OXIDE_ROWS
        for oxide in MELTS_OXIDE_ROWS:
            r = row_map_in.get(oxide.lower())
            if r is None:
                continue
            _set_excel_cell_preserving_blank(ws_in.cell(row=r, column=2), composition_row.get(oxide))
            rows_written_input += 1
        for name, value in {
            "Pressure": float(params.P1),
            "Temperature": float(params.T1),
            "log_fO2": float(params.fO2_offset),
        }.items():
            ref = _excel_defined_name_first_cell(wb, name)
            if ref is None:
                continue
            ws_named, r_named, c_named = ref
            _set_excel_cell_preserving_blank(ws_named.cell(row=r_named, column=c_named), value)
            rows_written_input += 1

    if "Sequences" in wb.sheetnames:
        ws_seq = wb["Sequences"]
        row_map_seq = _worksheet_label_row_map(ws_seq, row_min=1, row_max=120)
        seq_settings = {
            "T1": float(params.T1),
            "T2": float(params.T2),
            "ΔT": float(params.dT),
            "P1": float(params.P1),
            "P2": float(params.P2),
            "ΔP": float(params.dP),
            "fO2": float(params.fO2_offset),
            "Starting T": starting_T,
            "Min liq content": min_liq_content,
            "Fractionate": fractionate,
            "Phase 1": phase_1,
            "Phase 2": phase_2,
            "Phase 3": phase_3,
            "Formula": formula,
            "ΔH": delta_h,
            "ΔV": delta_v,
            "ΔS": delta_s,
        }
        for label, value in seq_settings.items():
            r = row_map_seq.get(str(label).strip().lower())
            if r is None:
                continue
            _set_excel_cell_preserving_blank(ws_seq.cell(row=r, column=2), value)
            rows_written_sequences += 1

    return {
        "rows_written_input_sheet": int(rows_written_input),
        "rows_written_sequences": int(rows_written_sequences),
        "input_sheet_oxide_row_order": input_order,
        "input_sheet_oxide_order_matches_expected": input_matches,
    }


def write_composition_to_melts_excel_template(
    *,
    template_workbook_path: Any,
    output_workbook_path: Any,
    composition: Optional[dict[str, Any]] = None,
    prepared_mc_csv_path: Optional[Any] = None,
    sample_label: Optional[str] = None,
    params: Optional[MELTSRunParams] = None,
    target_column_header: str = "P_Calc",
    mirror_input_and_sequences: bool = True,
    starting_T: Any = "wet liquidus",
    min_liq_content: Any = 1,
    fractionate: Any = "none",
    phase_1: Any = "quartz",
    phase_2: Any = "feldspar1",
    phase_3: Any = "feldspar2",
    formula: Any = "any two phases",
    delta_h: Any = 0.2,
    delta_v: Any = 0,
    delta_s: Any = 0,
) -> MeltsExcelTemplateWriteResult:
    """
    Write a single composition + run settings into the MELTS Excel (.xlsm) template.

    This preserves user-supplied composition values exactly and writes them into the
    workbook's expected row-label format (primarily `Multiple_Comp`, optionally also
    `Input` + `Sequences`) without adding any new sheets.
    """
    openpyxl = _require_openpyxl()
    if params is None:
        raise ValueError("params (MELTSRunParams) is required")

    comp_row, resolved_label = _extract_single_prepared_composition_row(
        composition=composition,
        prepared_mc_csv_path=prepared_mc_csv_path,
        sample_label=sample_label,
    )

    template_path = Path(template_workbook_path)
    output_path = Path(output_workbook_path)
    if not template_path.exists():
        raise FileNotFoundError(f"MELTS Excel template not found: {template_path}")
    _ensure_dir(output_path.parent)

    wb = openpyxl.load_workbook(template_path, keep_vba=True)
    try:
        mc_summary = _write_multiple_comp_column(
            wb,
            column_header=target_column_header,
            composition_row=comp_row,
            params=params,
            starting_T=starting_T,
            min_liq_content=min_liq_content,
            fractionate=fractionate,
            phase_1=phase_1,
            phase_2=phase_2,
            phase_3=phase_3,
            formula=formula,
            delta_h=delta_h,
            delta_v=delta_v,
            delta_s=delta_s,
        )
        mirror_summary = {
            "rows_written_input_sheet": 0,
            "rows_written_sequences": 0,
            "input_sheet_oxide_row_order": None,
            "input_sheet_oxide_order_matches_expected": None,
        }
        if mirror_input_and_sequences:
            mirror_summary = _write_input_and_sequences_sheet_snapshot(
                wb,
                composition_row=comp_row,
                params=params,
                starting_T=starting_T,
                min_liq_content=min_liq_content,
                fractionate=fractionate,
                phase_1=phase_1,
                phase_2=phase_2,
                phase_3=phase_3,
                formula=formula,
                delta_h=delta_h,
                delta_v=delta_v,
                delta_s=delta_s,
            )
        wb.save(output_path)
    finally:
        try:
            wb.close()
        except Exception:
            pass

    settings_written = {
        "Model": params.model,
        "Calculation": params.calculation,
        "T1": float(params.T1),
        "T2": float(params.T2),
        "ΔT": float(params.dT),
        "P1": float(params.P1),
        "P2": float(params.P2),
        "ΔP": float(params.dP),
        "fO2 offset": float(params.fO2_offset),
        "fO2 buffer": _normalize_redox_buffer_name(params.fO2_buffer) or params.fO2_buffer,
        "fO2 constraint": _coerce_excel_bool(params.fO2_constraint),
        "Starting T": starting_T,
        "Min liq content": min_liq_content,
        "Fractionate": fractionate,
        "Phase 1": phase_1,
        "Phase 2": phase_2,
        "Phase 3": phase_3,
        "Formula": formula,
        "ΔH": delta_h,
        "ΔV": delta_v,
        "ΔS": delta_s,
        "mirror_input_and_sequences": bool(mirror_input_and_sequences),
    }

    return MeltsExcelTemplateWriteResult(
        output_workbook_path=str(output_path),
        template_workbook_path=str(template_path),
        sample_label=resolved_label,
        target_column_header=str(mc_summary["target_column_header"]),
        target_column_index=int(mc_summary["target_column_index"]),
        multiple_comp_oxide_row_order=list(mc_summary["oxide_row_order"]),
        multiple_comp_oxide_order_matches_expected=bool(mc_summary["oxide_order_matches_expected"]),
        input_sheet_oxide_row_order=mirror_summary["input_sheet_oxide_row_order"],
        input_sheet_oxide_order_matches_expected=mirror_summary["input_sheet_oxide_order_matches_expected"],
        rows_written_multiple_comp=int(mc_summary["rows_written"]),
        rows_written_input_sheet=int(mirror_summary["rows_written_input_sheet"]),
        rows_written_sequences=int(mirror_summary["rows_written_sequences"]),
        settings_written=settings_written,
    )


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

    # Ensure nested multiprocessing spawns (composition -> pressure workers) can
    # re-import the run-local patched helper by module name from its own directory.
    bootstrap_marker = "# rMELTS pipeline bootstrap: prefer this run-local helper copy in spawned workers\n"
    if bootstrap_marker not in source_text:
        bootstrap = (
            "import sys as _rmelts_sys\n"
            "from pathlib import Path as _rmelts_Path\n"
            f"{bootstrap_marker}"
            "_rmelts_helper_dir = str(_rmelts_Path(__file__).resolve().parent)\n"
            "if _rmelts_helper_dir not in _rmelts_sys.path:\n"
            "    _rmelts_sys.path.insert(0, _rmelts_helper_dir)\n"
        )
        source_text = bootstrap + source_text

    if profile.reset_liquidus_solver_after_none:
        # Production hardening: if liquidus-search equilibrium calls fail/return None,
        # rebuild the thermoengine Equilibrate object before continuing or returning.
        # This targets the state-history-dependent internal matrix/projection mismatch
        # diagnosed in thermoengine._compute_a_and_qr (A @ P_nz).
        inject_after = "    dbg = 0\n"
        helper_fn = (
            "    _rmelts_liquidus_bulk_comp = composition\n"
            "    def _rmelts_reset_equil():\n"
            "        nonlocal equil, state\n"
            "        logging.warning(f\"LIQUIDUS_RESET_BEGIN T={current_T} P={P}\")\n"
            "        try:\n"
            "            equil = equilibrate.Equilibrate(equil.element_list, equil.phase_list)\n"
            "        except Exception as _rmelts_reset_exc:\n"
            "            logging.warning(f\"Liquidus solver reset failed at T={current_T}°C, P={P} MPa: {_rmelts_reset_exc}\")\n"
            "            logging.warning(f\"LIQUIDUS_RESET_RESEED_FAIL T={current_T} P={P} reason=reset_equil_failed\")\n"
            "            state = None\n"
            "            return\n"
            "        try:\n"
            "            state = equilibrate.EquilState(equil.element_list, equil.phase_list)\n"
            "            omni_phase = state.omni_phase()\n"
            "            state.set_phase_comp(omni_phase, _rmelts_liquidus_bulk_comp, input_as_elements=True)\n"
            "            logging.warning(f\"LIQUIDUS_RESET_RESEED_OK T={current_T} P={P}\")\n"
            "        except Exception as _rmelts_reseed_exc:\n"
            "            logging.warning(f\"LIQUIDUS_RESET_RESEED_FAIL T={current_T} P={P} reason={_rmelts_reseed_exc}\")\n"
            "            state = None\n"
        )
        if inject_after in source_text and "_rmelts_reset_equil" not in source_text:
            source_text = source_text.replace(inject_after, inject_after + helper_fn, 1)

        if profile.raw_liquidus_execute_with_reset:
            per_branch_prelude = ""
            if profile.fresh_liquidus_solver_per_branch:
                per_branch_prelude = (
                    "        _rmelts_reset_equil()\n"
                    "        state = None\n"
                    "        if bulk_comp is None:\n"
                    "            bulk_comp = _rmelts_liquidus_bulk_comp\n"
                    "            logging.warning(f\"LIQUIDUS_FRESH_BRANCH_CANONICAL_BULK T={round(temp_K-273.15, 2)} P={P}\")\n"
                    "        else:\n"
                    "            logging.warning(f\"LIQUIDUS_FRESH_BRANCH_CALL_BULK T={round(temp_K-273.15, 2)} P={P}\")\n"
                )
            raw_exec_helper = (
                "    def _rmelts_liquidus_execute(equil_obj, temp_K, pressure_bar, timeout=None, state=None, bulk_comp=None, con_deltaNNO=0, debug=0):\n"
                f"{per_branch_prelude}"
                "        try:\n"
                "            return equil_obj.execute(temp_K, pressure_bar, state=state, bulk_comp=bulk_comp, con_deltaNNO=con_deltaNNO, debug=debug)\n"
                "        except Exception as _rmelts_exec_exc:\n"
                "            logging.warning(f\"Liquidus raw execute failed at T={round(temp_K-273.15, 2)}°C, P={P} MPa: {_rmelts_exec_exc}\")\n"
                "            _rmelts_reset_equil()\n"
                "            try:\n"
                "                _rmelts_retry_bulk_comp = bulk_comp\n"
                "                if _rmelts_retry_bulk_comp is None:\n"
                "                    _rmelts_retry_bulk_comp = _rmelts_liquidus_bulk_comp\n"
                "                    logging.warning(f\"LIQUIDUS_RETRY_WITH_CANONICAL_BULK T={round(temp_K-273.15, 2)} P={P}\")\n"
                "                else:\n"
                "                    logging.warning(f\"LIQUIDUS_RETRY_WITH_CALL_BULK T={round(temp_K-273.15, 2)} P={P}\")\n"
                "                return equil.execute(temp_K, pressure_bar, state=None, bulk_comp=_rmelts_retry_bulk_comp, con_deltaNNO=con_deltaNNO, debug=debug)\n"
                "            except Exception as _rmelts_exec_retry_exc:\n"
                "                logging.warning(f\"Liquidus raw execute retry failed at T={round(temp_K-273.15, 2)}°C, P={P} MPa: {_rmelts_exec_retry_exc}\")\n"
                "                logging.warning(f\"LIQUIDUS_RETRY_FAILED T={round(temp_K-273.15, 2)} P={P}\")\n"
                "                _rmelts_reset_equil()\n"
                "                return None\n"
            )
            if "_rmelts_liquidus_execute" not in source_text:
                source_text = source_text.replace(helper_fn, helper_fn + raw_exec_helper, 1)

            liquidus_call_replacements = [
                (
                    "state = safe_equilibrium_execute(equil, current_T+273.15, P*10, timeout=timeout, \n"
                    "                                       bulk_comp=composition, con_deltaNNO=fO2_offset, debug=dbg)",
                    "state = _rmelts_liquidus_execute(equil, current_T+273.15, P*10, timeout=timeout, \n"
                    "                                       bulk_comp=composition, con_deltaNNO=fO2_offset, debug=dbg)",
                ),
                (
                    "state = safe_equilibrium_execute(equil, current_T+1+273.15, P*10, timeout=timeout, \n"
                    "                                        state=state, con_deltaNNO=fO2_offset, debug=dbg)",
                    "state = _rmelts_liquidus_execute(equil, current_T+1+273.15, P*10, timeout=timeout, \n"
                    "                                        state=state, con_deltaNNO=fO2_offset, debug=dbg)",
                ),
                (
                    "state = safe_equilibrium_execute(equil, current_T+273.15, P*10, timeout=timeout, \n"
                    "                                        state=state, con_deltaNNO=fO2_offset, debug=dbg)",
                    "state = _rmelts_liquidus_execute(equil, current_T+273.15, P*10, timeout=timeout, \n"
                    "                                        state=state, con_deltaNNO=fO2_offset, debug=dbg)",
                ),
                (
                    "state = safe_equilibrium_execute(equil, current_T+1+273.15, P*10, state=state,timeout=timeout, \n"
                    "                                         con_deltaNNO=fO2_offset, debug=dbg)",
                    "state = _rmelts_liquidus_execute(equil, current_T+1+273.15, P*10, state=state,timeout=timeout, \n"
                    "                                         con_deltaNNO=fO2_offset, debug=dbg)",
                ),
            ]
            for old_call, new_call in liquidus_call_replacements:
                if old_call in source_text:
                    source_text = source_text.replace(old_call, new_call, 1)

        # Initial liquidus midpoint failure -> reset before fallback return.
        old_initial_none = (
            "        if state is None:\n"
            "            logging.error(f\"Initial equilibrium calculation failed or timed out at T={current_T}°C\")\n"
            "            return T1  # Return fallback temperature\n"
        )
        new_initial_none = (
            "        if state is None:\n"
            "            logging.error(f\"Initial equilibrium calculation failed or timed out at T={current_T}°C\")\n"
            "            _rmelts_reset_equil()\n"
            "            return T1  # Return fallback temperature\n"
        )
        if old_initial_none in source_text:
            source_text = source_text.replace(old_initial_none, new_initial_none, 1)

        # Initial probe failure -> reset before fallback return.
        old_initial_probe_none = (
            "            if state is None:\n"
            "                logging.error(f\"Initial equilibrium calculation failed or timed out at T={current_T}°C\")\n"
            "                return T1  # Return fallback temperature\n"
        )
        new_initial_probe_none = (
            "            if state is None:\n"
            "                logging.error(f\"Initial equilibrium calculation failed or timed out at T={current_T}°C\")\n"
            "                _rmelts_reset_equil()\n"
            "                return T1  # Return fallback temperature\n"
        )
        if old_initial_probe_none in source_text:
            source_text = source_text.replace(old_initial_probe_none, new_initial_probe_none, 1)

        # Probe failure in main liquidus loop -> reset before fallback return.
        old_loop_probe_none = (
            "                if state is None:\n"
            "                    logging.error(f\"Equilibrium calculation failed or timed out at T={current_T}°C\")\n"
            "                    return T1  # Return fallback temperature\n"
        )
        new_loop_probe_none = (
            "                if state is None:\n"
            "                    logging.error(f\"Equilibrium calculation failed or timed out at T={current_T}°C\")\n"
            "                    _rmelts_reset_equil()\n"
            "                    return T1  # Return fallback temperature\n"
        )
        if old_loop_probe_none in source_text:
            source_text = source_text.replace(old_loop_probe_none, new_loop_probe_none, 1)

        # Generic liquidus-search exceptions -> reset before fallback return.
        old_initial_except = (
            "    except Exception as e:\n"
            "        logging.error(f\"Initial calculation error: {e}\")\n"
            "        return T1\n"
        )
        new_initial_except = (
            "    except Exception as e:\n"
            "        logging.error(f\"Initial calculation error: {e}\")\n"
            "        _rmelts_reset_equil()\n"
            "        return T1\n"
        )
        if old_initial_except in source_text:
            source_text = source_text.replace(old_initial_except, new_initial_except, 1)

        old_probe_except = (
            "        except Exception as e:\n"
            "            logging.error(f\"Initial calculation error: {e}\")\n"
            "            return T1\n"
        )
        new_probe_except = (
            "        except Exception as e:\n"
            "            logging.error(f\"Initial calculation error: {e}\")\n"
            "            _rmelts_reset_equil()\n"
            "            return T1\n"
        )
        if old_probe_except in source_text:
            source_text = source_text.replace(old_probe_except, new_probe_except, 1)

        old_loop_except = (
            "        except Exception as e:\n"
            "            print(e)\n"
            "            return T1\n"
        )
        new_loop_except = (
            "        except Exception as e:\n"
            "            print(e)\n"
            "            _rmelts_reset_equil()\n"
            "            return T1\n"
        )
        if old_loop_except in source_text:
            source_text = source_text.replace(old_loop_except, new_loop_except, 1)

    if profile.safe_execute_reraise_non_timeout:
        safe_exec_except_block_old = (
            "    except TimeoutError as e:\n"
            "        logging.warning(f\"Equilibrium calculation timed out: T={T:.1f}K, P={P:.1f}bar - {str(e)}\")\n"
            "        return None\n"
            "        \n"
            "    except Exception as e:\n"
            "        logging.warning(f\"Equilibrium calculation failed: T={T:.1f}K, P={P:.1f}bar - {str(e)}\")\n"
            "        return None\n"
        )
        safe_exec_except_block_new = (
            "    except TimeoutError as e:\n"
            "        logging.warning(f\"Equilibrium calculation timed out: T={T:.1f}K, P={P:.1f}bar - {str(e)}\")\n"
            "        return None\n"
            "        \n"
            "    except Exception as e:\n"
            "        logging.warning(f\"Equilibrium calculation failed: T={T:.1f}K, P={P:.1f}bar - {str(e)}\")\n"
            "        raise\n"
        )
        if safe_exec_except_block_old in source_text:
            source_text = source_text.replace(safe_exec_except_block_old, safe_exec_except_block_new, 1)

    if profile.fix_liquidus_timeout_loop:
        # 0) Track immutable physical liquidus-search bounds and restart counters so
        # recovery logic cannot drift to unphysical temperatures.
        bounds_anchor = "    timeout = 10\n"
        bounds_inject = (
            "    _rmelts_T_upper_bound = T1\n"
            "    _rmelts_T_lower_bound = T2\n"
            "    _rmelts_liquidus_restart_count = 0\n"
            "    _rmelts_liquidus_max_restarts = 6\n"
        )
        if (
            "def find_wet_liquidus(" in source_text
            and "_rmelts_T_upper_bound = T1" not in source_text
            and bounds_anchor in source_text
        ):
            source_text = source_text.replace(bounds_anchor, bounds_anchor + bounds_inject, 1)

        # 0.5) Guard the top of the liquidus loop so corrupted bracket/current_T values
        # are reset before another equilibrium execute call is attempted.
        loop_anchors = [
            "    while (T1 > T2) and i < 50:\n\n        try:\n",
            "    while (T1 > T2) and i < 50:\n        try:\n",
        ]
        fresh_iteration_lines = (
            "        logging.warning(f\"LIQUIDUS_FRESH_ITERATION_RESET T={current_T} P={P}\")\n"
            "        _rmelts_reset_equil()\n"
            "        state = None\n"
            "\n"
            if profile.fresh_liquidus_solver_per_iteration
            else ""
        )
        loop_guard = (
            "    while (T1 > T2) and i < 50:\n"
            "\n"
            "        _rmelts_bounds_invalid = (\n"
            "            (not np.isfinite(current_T)) or\n"
            "            (not np.isfinite(T1)) or\n"
            "            (not np.isfinite(T2)) or\n"
            "            (current_T < _rmelts_T_lower_bound) or\n"
            "            (current_T > _rmelts_T_upper_bound) or\n"
            "            (T1 > _rmelts_T_upper_bound) or\n"
            "            (T2 < _rmelts_T_lower_bound)\n"
            "        )\n"
            "        if _rmelts_bounds_invalid:\n"
            "            logging.error(\n"
            "                f\"Liquidus search invalid state detected (current_T={current_T}, T1={T1}, T2={T2}, P={P} MPa); restarting bounded search\"\n"
            "            )\n"
            "            _rmelts_reset_equil()\n"
            "            _rmelts_liquidus_restart_count = _rmelts_liquidus_restart_count + 1\n"
            "            if _rmelts_liquidus_restart_count > _rmelts_liquidus_max_restarts:\n"
            "                logging.error(f\"Liquidus search exceeded max bounded restarts at P={P} MPa\")\n"
            "                return _rmelts_T_upper_bound\n"
            "            T1 = _rmelts_T_upper_bound\n"
            "            T2 = _rmelts_T_lower_bound\n"
            "            current_T = round((T1+T2)/2)\n"
            "            state = None\n"
            "            i = i + 1\n"
            "            continue\n"
            "\n"
            f"{fresh_iteration_lines}"
            "        try:\n"
        )
        if "_rmelts_bounds_invalid" not in source_text:
            for loop_anchor in loop_anchors:
                if loop_anchor in source_text:
                    source_text = source_text.replace(loop_anchor, loop_guard, 1)
                    break

        # 1) find_wet_liquidus timeout branch can loop forever because i is not
        # incremented on repeated timeouts and current_T can march upward forever.
        old = (
            "                current_T = current_T+25  # continue with higher temperature\n"
            "                continue\n"
        )
        reset_lines = ""
        if profile.reset_liquidus_solver_after_none:
            reset_lines = (
                "                _rmelts_reset_equil()\n"
            )
        new = (
            f"{reset_lines}"
            "                _rmelts_liquidus_restart_count = _rmelts_liquidus_restart_count + 1\n"
            "                _rmelts_next_T = min(_rmelts_T_upper_bound, current_T+25)\n"
            "                if _rmelts_next_T == current_T:\n"
            "                    logging.error(f\"Liquidus search nonprogressing after repeated failures at T={current_T}°C, P={P} MPa\")\n"
            "                    if _rmelts_liquidus_restart_count > _rmelts_liquidus_max_restarts:\n"
            "                        logging.error(f\"Liquidus search exceeded max bounded restarts at P={P} MPa\")\n"
            "                        return _rmelts_T_upper_bound\n"
            "                    T1 = _rmelts_T_upper_bound\n"
            "                    T2 = _rmelts_T_lower_bound\n"
            "                    current_T = round((T1+T2)/2)\n"
            "                    state = None\n"
            "                    i = i + 1\n"
            "                    continue\n"
            "                current_T = max(_rmelts_T_lower_bound, _rmelts_next_T)  # continue with higher temperature (physically bounded)\n"
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
        main_none_extra = ""
        if profile.reset_main_solver_after_timeout:
            main_none_extra += (
                "                    try:\n"
                "                        equil = equilibrate.Equilibrate(elm_sys_local, phs_sys_local)\n"
                "                        state = equilibrate.EquilState(equil.element_list,equil.phase_list)\n"
                "                        omni_phase = state.omni_phase()\n"
                "                        state.set_phase_comp(omni_phase,blk_cmp,input_as_elements=True)\n"
                "                    except Exception as _rmelts_main_reset_exc:\n"
                "                        print(f\"Error resetting solver after timeout at P={pressure}: {_rmelts_main_reset_exc}\")\n"
                "                        state = None\n"
            )
        if profile.bound_main_loop_failures:
            main_none_extra += (
                "                    _rmelts_main_consecutive_failures = _rmelts_main_consecutive_failures + 1\n"
                "                    if _rmelts_main_consecutive_failures >= _rmelts_main_max_consecutive_failures:\n"
                "                        print(f\"Aborting pressure path at P={pressure} after repeated timeout/None returns\")\n"
                "                        solidus = True\n"
                "                        break\n"
            )
            success_counter_reset = "                _rmelts_main_consecutive_failures = 0\n"
        else:
            success_counter_reset = ""
        old_exec = (
            "                state = equil.execute(temp + 273.15, pressure * 10, bulk_comp=blk_cmp, con_deltaNNO=fO2_offset, debug=0)\n"
            "\n"
            "                t1 = time.time()\n"
            "                calc_time += (t1 - t0)\n"
        )
        timeout_literal = repr(float(profile.main_execute_timeout_s))
        new_exec = (
            f"                state = safe_equilibrium_execute(equil, temp + 273.15, pressure * 10, timeout={timeout_literal},\n"
            "                                              bulk_comp=blk_cmp, con_deltaNNO=fO2_offset, debug=0)\n"
            "                if state is None:\n"
            "                    print(f'Error at T={temp}, P={pressure}: timeout in execute')\n"
            f"{main_none_extra}"
            f"{continue_line}"
            "\n"
            f"{success_counter_reset}"
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

    if profile.reset_solver_after_liquidus_presearch:
        liq_anchor = "        temp = find_wet_liquidus(equil, T1, T2, pressure, 50, blk_cmp, fO2_offset, verbose)\n"
        liq_reset_block = (
            "        temp = find_wet_liquidus(equil, T1, T2, pressure, 50, blk_cmp, fO2_offset, verbose)\n"
            "        # pipeline patch: reset solver after liquidus search so the main TP sequence\n"
            "        # starts from a fresh numerical state for this pressure path.\n"
            "        try:\n"
            "            equil = equilibrate.Equilibrate(elm_sys_local, phs_sys_local)\n"
            "            state = equilibrate.EquilState(equil.element_list,equil.phase_list)\n"
            "            omni_phase = state.omni_phase()\n"
            "            state.set_phase_comp(omni_phase,blk_cmp,input_as_elements=True)\n"
            "        except Exception as _rmelts_main_reset_exc:\n"
            "            print(f\"Error resetting solver after liquidus at P={pressure}: {_rmelts_main_reset_exc}\")\n"
            "            state = None\n"
        )
        if liq_anchor in source_text and "reset solver after liquidus search" not in source_text:
            source_text = source_text.replace(liq_anchor, liq_reset_block, 1)

    if profile.bound_main_loop_failures:
        loop_anchor = "        # Run temperature sequence for this pressure\n        for step in range(N_runs + 1):\n"
        loop_prefix = (
            "        # Run temperature sequence for this pressure\n"
            "        _rmelts_main_consecutive_failures = 0\n"
            "        _rmelts_main_max_consecutive_failures = 6\n"
            "        _rmelts_main_temp_upper = T1\n"
            "        _rmelts_main_temp_lower = T2\n"
            "        for step in range(N_runs + 1):\n"
        )
        init_marker = "_rmelts_main_max_consecutive_failures = "
        if loop_anchor in source_text and init_marker not in source_text:
            source_text = source_text.replace(loop_anchor, loop_prefix, 1)
        if init_marker not in source_text:
            # Fallback for helper source variants where nearby patches slightly alter
            # spacing/blank lines around the loop anchor.
            fallback_for = "        for step in range(N_runs + 1):\n"
            if fallback_for in source_text:
                source_text = source_text.replace(
                    fallback_for,
                    "        _rmelts_main_consecutive_failures = 0\n"
                    "        _rmelts_main_max_consecutive_failures = 6\n"
                    "        _rmelts_main_temp_upper = T1\n"
                    "        _rmelts_main_temp_lower = T2\n"
                    + fallback_for,
                    1,
                )

        guard_old = "            if temp < T2 or solidus:\n"
        guard_new = "            if temp < T2 or temp > T1 or solidus:\n"
        if guard_old in source_text:
            source_text = source_text.replace(guard_old, guard_new, 1)

        except_old = (
            "            except Exception as e:\n"
            "                print(f\"Error at T={temp}, P={pressure}: {e}\")\n"
            "                # Continue to next temperature step\n"
            "                pass\n"
        )
        except_reset_lines = ""
        if profile.reset_main_solver_after_timeout:
            except_reset_lines = (
                "                try:\n"
                "                    equil = equilibrate.Equilibrate(elm_sys_local, phs_sys_local)\n"
                "                    state = equilibrate.EquilState(equil.element_list,equil.phase_list)\n"
                "                    omni_phase = state.omni_phase()\n"
                "                    state.set_phase_comp(omni_phase,blk_cmp,input_as_elements=True)\n"
                "                except Exception as _rmelts_main_reset_exc:\n"
                "                    print(f\"Error resetting solver after main-loop exception at P={pressure}: {_rmelts_main_reset_exc}\")\n"
                "                    state = None\n"
            )
        except_new = (
            "            except Exception as e:\n"
            "                print(f\"Error at T={temp}, P={pressure}: {e}\")\n"
            f"{except_reset_lines}"
            "                _rmelts_main_consecutive_failures = _rmelts_main_consecutive_failures + 1\n"
            "                if _rmelts_main_consecutive_failures >= _rmelts_main_max_consecutive_failures:\n"
            "                    print(f\"Aborting pressure path at P={pressure} after repeated main-loop errors\")\n"
            "                    solidus = True\n"
            "                # Continue to next temperature step\n"
            "                pass\n"
        )
        if except_old in source_text:
            source_text = source_text.replace(except_old, except_new, 1)
    return source_text


def _prepare_run_local_helper_copy(
    helper_dir: Path,
    run_dir: Path,
    *,
    helper_source_path: Optional[Path] = None,
    patch_profile: Optional[Any] = None,
) -> Path:
    """
    Create a run-scoped helper copy so multiprocessing spawn workers import the
    patched helper from the run directory without modifying Liam's source file.
    """
    helper_src = Path(helper_source_path).expanduser().resolve() if helper_source_path is not None else (helper_dir / "MeltsHelperFunctions.py")
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
    helper_implementation: Optional[str] = None,
    patch_pressure_calc: bool = True,
) -> Any:
    impl_spec = _resolve_helper_implementation_spec(helper_implementation)
    helper_path = Path(impl_spec.source_path).resolve() if impl_spec.source_path is not None else (helper_dir / "MeltsHelperFunctions.py")
    if not helper_path.exists():
        raise FileNotFoundError(f"Helper module not found: {helper_path}")

    import_path = helper_path
    effective_patch_profile = patch_profile if patch_profile is not None else impl_spec.patch_profile
    if run_dir is not None:
        run_dir = Path(run_dir).expanduser().resolve()
        _ensure_dir(run_dir)
        import_path = _prepare_run_local_helper_copy(
            helper_dir,
            run_dir,
            helper_source_path=helper_path,
            patch_profile=effective_patch_profile,
        )
        run_dir_str = str(run_dir)
        if run_dir_str not in sys.path:
            sys.path.insert(0, run_dir_str)
        # Make the run-local helper importable by nested multiprocessing spawns
        # that reconstruct sys.path from environment/Python startup defaults.
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        pythonpath_parts = [p for p in existing_pythonpath.split(os.pathsep) if p]
        if run_dir_str not in pythonpath_parts:
            os.environ["PYTHONPATH"] = os.pathsep.join([run_dir_str] + pythonpath_parts)

    # Use a stable canonical module name so multiprocessing spawn workers can re-import
    # helper functions defined in this module (e.g., process_single_composition_parallel).
    module_name = "MeltsHelperFunctions"
    helper_dir_str = str(helper_dir)
    helper_src_dir_str = str(helper_path.parent)
    if run_dir is not None:
        run_dir_str = str(run_dir)
        # Keep the run-local patched helper directory ahead of the original helper
        # directory so nested multiprocessing spawns import the patched helper copy.
        if run_dir_str in sys.path:
            sys.path = [p for p in sys.path if p != run_dir_str]
            sys.path.insert(0, run_dir_str)
        if impl_spec.source_path is not None:
            if helper_src_dir_str not in sys.path:
                sys.path.insert(1 if sys.path and sys.path[0] == run_dir_str else 0, helper_src_dir_str)
        if helper_dir_str not in sys.path:
            insert_at = 2 if (
                sys.path
                and sys.path[0] == run_dir_str
                and impl_spec.source_path is not None
                and helper_src_dir_str in sys.path
            ) else (1 if sys.path and sys.path[0] == run_dir_str else 0)
            sys.path.insert(insert_at, helper_dir_str)
    else:
        if impl_spec.source_path is not None and helper_src_dir_str not in sys.path:
            sys.path.insert(0, helper_src_dir_str)
        if helper_dir_str not in sys.path:
            sys.path.insert(1 if impl_spec.source_path is not None and sys.path and sys.path[0] == helper_src_dir_str else 0, helper_dir_str)

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
    helper_implementation: Optional[str] = None,
) -> list[dict[str, Any]]:
    module = _load_helper_module(helper_dir, run_dir=run_dir, helper_implementation=helper_implementation)
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
    helper_implementation_requested = os.environ.get(_HELPER_IMPLEMENTATION_ENV_VAR, "").strip() or "default"
    helper_impl_spec = _resolve_helper_implementation_spec(None)
    cleanup_before_snapshot = _snapshot_descendant_processes()
    cleanup_summary: dict[str, Any] = {}
    deltaqfm_enrichment_summary: dict[str, Any] = {}

    try:
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
                    helper_implementation=helper_implementation_requested if helper_implementation_requested != "default" else None,
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
                        helper_implementation=helper_implementation_requested if helper_implementation_requested != "default" else None,
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
    finally:
        cleanup_summary = _cleanup_descendant_processes(cleanup_before_snapshot)

    elapsed = time.time() - start

    try:
        deltaqfm_enrichment_summary = _enrich_deltaqfm_in_generated_workbooks(run_dir)
    except Exception as exc:
        deltaqfm_enrichment_summary = {"error": str(exc)}

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

    print(
        "cleanup attempted={attempted} found={found} terminated={terminated} killed={killed} "
        "remaining={remaining} duration_s={duration}".format(
            attempted=cleanup_summary.get("cleanup_attempted"),
            found=cleanup_summary.get("cleanup_descendants_found"),
            terminated=cleanup_summary.get("cleanup_terminated_count"),
            killed=cleanup_summary.get("cleanup_killed_count"),
            remaining=cleanup_summary.get("cleanup_remaining_count"),
            duration=cleanup_summary.get("cleanup_duration_s"),
        )
    )
    if deltaqfm_enrichment_summary:
        print(
            "deltaQFM workbooks_seen={seen} workbooks_updated={updated} rows_updated={rows} errors={errs}".format(
                seen=deltaqfm_enrichment_summary.get("workbooks_seen"),
                updated=deltaqfm_enrichment_summary.get("workbooks_updated"),
                rows=deltaqfm_enrichment_summary.get("rows_updated"),
                errs=(len(deltaqfm_enrichment_summary.get("errors", [])) if isinstance(deltaqfm_enrichment_summary.get("errors"), list) else None),
            )
        )

    helper_patch_profile_effective = (
        _resolve_helper_patch_profile(helper_impl_spec.patch_profile).name
        if helper_impl_spec.patch_profile is not None
        else _resolve_helper_patch_profile().name
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
        "helper_implementation_requested": helper_implementation_requested,
        "helper_implementation_resolved": helper_impl_spec.name,
        "helper_patch_profile": helper_patch_profile_effective,
        "process_cleanup": _json_safe(cleanup_summary),
        "deltaqfm_enrichment": _json_safe(deltaqfm_enrichment_summary),
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
            "helper_implementation_source_path": (None if helper_impl_spec.source_path is None else str(helper_impl_spec.source_path)),
        },
        "results": _json_safe(results),
        "manifest_preview": _json_safe(manifest_df.to_dict(orient="records")),
        "cleanup": _json_safe(cleanup_summary),
        "deltaqfm_enrichment": _json_safe(deltaqfm_enrichment_summary),
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
    "MeltsExcelTemplateWriteResult",
    "MC_to_csv_rMELTS",
    "normalize_aplite_xrf_to_rowwise_mc",
    "write_composition_to_melts_excel_template",
    "rMELTS_run",
    "rMELTS_run_staged_first_appearance",
    "rMELTS_geobarometry_basis",
    "MELTS_REQUIRED_15_ROWS",
    "MELTS_ALL_ROWS",
    "_build_melts_input_wide_dataframe",
]
