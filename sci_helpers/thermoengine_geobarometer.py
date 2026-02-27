"""Single-driver ThermoEngine geobarometer and liquidus workflow."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime
import io
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import optimize as opt

from .liam_pressure_runner import _ensure_thermoengine_importable, preflight_liam_runtime

REQUIRED_OXIDES: tuple[str, ...] = (
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
)

ELM_SYS: tuple[str, ...] = (
    "H",
    "O",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "K",
    "Ca",
    "Ti",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
)

DEFAULT_PHASE_KEYS: tuple[str, ...] = ("Quartz", "Feldspar", "Spinel", "Opx", "RhomOx")

CANONICAL_BENCHMARK_OXIDES: dict[str, float] = {
    "SiO2": 77.5,
    "TiO2": 0.08,
    "Al2O3": 12.5,
    "Fe2O3": 0.207,
    "Cr2O3": 0.0,
    "FeO": 0.473,
    "MnO": 0.0,
    "MgO": 0.03,
    "NiO": 0.0,
    "CoO": 0.0,
    "CaO": 0.43,
    "Na2O": 3.98,
    "K2O": 4.88,
    "P2O5": 0.0,
    "H2O": 10.0,
}


@dataclass(frozen=True)
class GeobarometerSampleInput:
    """Normalized per-sample inputs for geobarometer workflows."""

    sample_id: str
    oxides_wt: dict[str, float]
    fO2_offset: float = 0.0
    T_init_K: float = 1034.0
    P_init_bar: float = 1750.0
    T0_C: float = 1100.0
    Tf_C: float = 700.0
    dT_C: float = 1.0
    P0_MPa: float = 400.0
    Pf_MPa: float = 50.0
    dP_MPa: float = 25.0
    date: str = ""
    notes: str = ""
    plumi: float | str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeobarometerRunConfig:
    """Run-level settings for the single-driver geobarometer script."""

    input_csv: Path | str
    output_dir: Path | str
    mode: str = "all"
    pressure_bracket_bar: tuple[float, float] | None = None
    pressure_grid_mpa: tuple[float, float, float] | None = None
    benchmark_first: bool = True
    benchmark_tolerance_c: float = 3.0
    benchmark_tolerance_mpa: float = 10.0
    benchmark_objective_ratio: float = 2.0
    benchmark_quartz_tolerance_c: float = 3.0
    thermoengine_src_root: Path | str | None = None
    phase_keys: tuple[str, ...] = DEFAULT_PHASE_KEYS
    temperature_bracket_k: tuple[float, float] = (500.0, 2000.0)
    temperature_secant_step_k: float = 25.0
    delta_residual_for_dp: float = 1.0
    verbose: bool = False

    benchmark_baseline_t_c: float = 759.7242264918051
    benchmark_baseline_p_mpa: float = 176.97042020212393
    benchmark_baseline_objective: float = 0.0012265899859114702
    benchmark_baseline_quartz_3000bar_t_c: float = 758.0790896483558


@dataclass
class _ThermoEngineContext:
    core: Any
    phases: Any
    model: Any
    equilibrate: Any
    model_db: Any
    liquid: Any
    feldspar: Any
    quartz: Any
    spinel: Any
    opx: Any
    rhomox: Any
    water: Any
    elm_sys: tuple[str, ...]
    phase_objects: dict[str, Any]
    con_phase_d: dict[str, np.ndarray]
    equil_liquid_water: Any
    equil_with_solids: Any


@dataclass
class _StateCache:
    state: Any | None = None


def _normalize_column_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).strip().lower())


def _find_column(columns: Sequence[str], *aliases: str) -> str | None:
    normalized = {_normalize_column_name(col): col for col in columns}
    for alias in aliases:
        key = _normalize_column_name(alias)
        if key in normalized:
            return normalized[key]
    return None


def _coerce_required_float(row: pd.Series, col: str, field_name: str, sample_id: str) -> float:
    value = pd.to_numeric(row[col], errors="coerce")
    if pd.isna(value):
        raise ValueError(f"Sample '{sample_id}' has non-numeric {field_name}: {row[col]!r}")
    return float(value)


def _coerce_optional_float(
    row: pd.Series,
    columns: Sequence[str],
    default: float,
    *aliases: str,
) -> float:
    col = _find_column(columns, *aliases)
    if col is None:
        return float(default)
    value = pd.to_numeric(row[col], errors="coerce")
    if pd.isna(value):
        return float(default)
    return float(value)


def load_sample_inputs_from_csv(path: str | Path) -> list[GeobarometerSampleInput]:
    """Load and normalize row-oriented geobarometer sample input CSV."""
    input_path = Path(path).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV does not exist: {input_path}")

    table = pd.read_csv(input_path)
    if table.empty:
        raise ValueError(f"Input CSV has no rows: {input_path}")

    sample_col = _find_column(table.columns, "sample_id", "sample id", "sample")
    if sample_col is None:
        raise ValueError("Input CSV is missing required 'sample_id' column.")

    missing_oxides = [oxide for oxide in REQUIRED_OXIDES if _find_column(table.columns, oxide) is None]
    if missing_oxides:
        missing_text = ", ".join(missing_oxides)
        raise ValueError(f"Input CSV is missing required oxide columns: {missing_text}")

    samples: list[GeobarometerSampleInput] = []
    for idx, row in table.iterrows():
        sample_id = str(row[sample_col]).strip()
        if not sample_id or sample_id.lower() == "nan":
            raise ValueError(f"Row {idx} has empty sample_id.")

        oxides: dict[str, float] = {}
        for oxide in REQUIRED_OXIDES:
            col = _find_column(table.columns, oxide)
            assert col is not None
            oxides[oxide] = _coerce_required_float(row, col, oxide, sample_id)

        f_o2_offset = _coerce_optional_float(row, table.columns, 0.0, "fO2_offset", "fO2 offset")
        t_init_k = _coerce_optional_float(row, table.columns, 1034.0, "T_init_K", "t init k")
        p_init_bar = _coerce_optional_float(row, table.columns, 1750.0, "P_init_bar", "p init bar")

        t0_c = _coerce_optional_float(row, table.columns, 1100.0, "T0_C", "T0", "t1")
        tf_c = _coerce_optional_float(row, table.columns, 700.0, "Tf_C", "Tf", "t2")
        dt_c = _coerce_optional_float(row, table.columns, 1.0, "dT_C", "dT", "delta_t")
        p0_mpa = _coerce_optional_float(row, table.columns, 400.0, "P0_MPa", "P0", "p1")
        pf_mpa = _coerce_optional_float(row, table.columns, 50.0, "Pf_MPa", "Pf", "p2")
        dp_mpa = _coerce_optional_float(row, table.columns, 25.0, "dP_MPa", "dP", "delta_p")

        date_col = _find_column(table.columns, "date")
        notes_col = _find_column(table.columns, "notes", "note")
        plumi_col = _find_column(table.columns, "plumi")

        date = "" if date_col is None or pd.isna(row[date_col]) else str(row[date_col]).strip()
        notes = "" if notes_col is None or pd.isna(row[notes_col]) else str(row[notes_col]).strip()
        plumi: float | str | None
        if plumi_col is None or pd.isna(row[plumi_col]):
            plumi = None
        else:
            parsed_plumi = pd.to_numeric(row[plumi_col], errors="coerce")
            plumi = float(parsed_plumi) if not pd.isna(parsed_plumi) else str(row[plumi_col]).strip()

        known_columns = {
            sample_col,
            *(col for col in (_find_column(table.columns, oxide) for oxide in REQUIRED_OXIDES) if col),
            *(
                col
                for col in (
                    _find_column(table.columns, "fO2_offset", "fO2 offset"),
                    _find_column(table.columns, "T_init_K", "t init k"),
                    _find_column(table.columns, "P_init_bar", "p init bar"),
                    _find_column(table.columns, "T0_C", "T0", "t1"),
                    _find_column(table.columns, "Tf_C", "Tf", "t2"),
                    _find_column(table.columns, "dT_C", "dT", "delta_t"),
                    _find_column(table.columns, "P0_MPa", "P0", "p1"),
                    _find_column(table.columns, "Pf_MPa", "Pf", "p2"),
                    _find_column(table.columns, "dP_MPa", "dP", "delta_p"),
                    date_col,
                    notes_col,
                    plumi_col,
                )
                if col
            ),
        }
        metadata = {
            col: row[col]
            for col in table.columns
            if col not in known_columns and not pd.isna(row[col])
        }

        samples.append(
            GeobarometerSampleInput(
                sample_id=sample_id,
                oxides_wt=oxides,
                fO2_offset=f_o2_offset,
                T_init_K=t_init_k,
                P_init_bar=p_init_bar,
                T0_C=t0_c,
                Tf_C=tf_c,
                dT_C=dt_c,
                P0_MPa=p0_mpa,
                Pf_MPa=pf_mpa,
                dP_MPa=dp_mpa,
                date=date,
                notes=notes,
                plumi=plumi,
                metadata=metadata,
            )
        )

    return samples


def convert_oxide_wt_to_blk_cmp(
    oxide_wt: Mapping[str, float],
    core_module: Any,
    liquid_phase: Any,
    elm_sys: Sequence[str] = ELM_SYS,
) -> np.ndarray:
    """Convert oxide wt% to elemental bulk composition in elm_sys order."""
    mol_oxides = core_module.chem.format_mol_oxide_comp(
        oxide_wt,
        convert_grams_to_moles=True,
    )
    moles_end, _oxide_residual = liquid_phase.calc_endmember_comp(
        mol_oxide_comp=mol_oxides,
        method="intrinsic",
        output_residual=True,
    )
    if not liquid_phase.test_endmember_comp(moles_end):
        raise ValueError("Calculated liquid endmember composition is infeasible.")

    mol_elm = liquid_phase.convert_endmember_comp(moles_end, output="moles_elements")
    periodic = core_module.chem.PERIODIC_ORDER.tolist()
    return np.array([mol_elm[periodic.index(elm)] for elm in elm_sys], dtype=float)


def compute_mu_vectors_from_liquid_potentials(mu_liq: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Compute feldspar/quartz chemical potential vectors from liquid potentials."""
    if mu_liq.ndim != 1:
        raise ValueError("mu_liq must be a 1D vector.")
    if mu_liq.size <= 12:
        raise ValueError("mu_liq must contain at least 13 components for notebook mapping.")

    mu_fld = np.array(
        [
            5.0 * mu_liq[0] / 2.0 + mu_liq[2] / 2.0 + mu_liq[11] / 2.0,
            mu_liq[0] + mu_liq[2] + mu_liq[10],
            2.0 * mu_liq[0] + mu_liq[12],
        ],
        dtype=float,
    )
    mu_qtz = np.array([mu_liq[0]], dtype=float)
    return mu_fld, mu_qtz


def affinity_objective_from_affinities(affinity_feldspar: float, affinity_quartz: float) -> float:
    """Notebook geobarometer objective function."""
    return float((affinity_feldspar / 13.0) ** 2 + (affinity_quartz / 3.0) ** 2)


def build_phase_mu_from_con_matrix(
    con_phase_matrix: np.ndarray,
    mu_liq: np.ndarray,
    m_liq: np.ndarray,
) -> np.ndarray:
    """Build phase chemical potential vector from liquid potentials and conversion matrix."""
    mu_phase: list[float] = []
    for i in range(con_phase_matrix.shape[0]):
        total = 0.0
        valid = True
        for j in range(con_phase_matrix.shape[1]):
            coeff = float(con_phase_matrix[i, j])
            if coeff != 0.0 and m_liq[j] == 0:
                valid = False
                break
            total += coeff * float(mu_liq[j])
        mu_phase.append(total if valid else 0.0)
    return np.array(mu_phase, dtype=float)


def fit_parabola_pressure_minimum(
    pressures_mpa: Sequence[float],
    residuals: Sequence[float],
    delta_residual: float = 1.0,
) -> dict[str, Any]:
    """Fit local parabola around residual minimum and estimate P* and dP."""
    p = np.asarray(pressures_mpa, dtype=float)
    r = np.asarray(residuals, dtype=float)

    mask = np.isfinite(p) & np.isfinite(r)
    p = p[mask]
    r = r[mask]
    if p.size < 3:
        return {
            "success": False,
            "reason": "insufficient_points",
            "pressure_mpa": np.nan,
            "residual_min": np.nan,
            "coefficients": (np.nan, np.nan, np.nan),
            "dP_mpa": np.nan,
        }

    idx_min = int(np.nanargmin(r))
    i0 = max(0, idx_min - 2)
    i1 = min(p.size, idx_min + 3)
    p_fit = p[i0:i1]
    r_fit = r[i0:i1]
    if p_fit.size < 3:
        p_fit = p
        r_fit = r

    coeff = np.polyfit(p_fit, r_fit, deg=2)
    a, b, c = (float(coeff[0]), float(coeff[1]), float(coeff[2]))
    if abs(a) <= np.finfo(float).eps:
        return {
            "success": False,
            "reason": "near_linear_fit",
            "pressure_mpa": np.nan,
            "residual_min": np.nan,
            "coefficients": (a, b, c),
            "dP_mpa": np.nan,
        }

    p_est = -b / (2.0 * a)
    r_min = a * p_est * p_est + b * p_est + c
    if a > 0 and delta_residual >= 0:
        d_p = math.sqrt(delta_residual / a)
    else:
        d_p = float("nan")

    return {
        "success": True,
        "reason": None,
        "pressure_mpa": float(p_est),
        "residual_min": float(r_min),
        "coefficients": (a, b, c),
        "dP_mpa": float(d_p),
    }


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_to_json_safe(v) for v in value.tolist()]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    return value


def _get_phase_compat(model_db: Any, primary_code: str, fallback_code: str) -> Any:
    try:
        return model_db.get_phase(primary_code)
    except Exception:
        return model_db.get_phase(fallback_code)


def _build_water_phase(model_db: Any, phases_mod: Any) -> Any:
    try:
        # Match the notebook convention used for geobarometer objectives.
        return phases_mod.PurePhase("WaterMelts", "H2O", calib=False)
    except Exception:
        pass

    for code in ("H2O", "Water", "Fl", "Fluid"):
        try:
            return model_db.get_phase(code)
        except Exception:
            continue
    return phases_mod.PurePhase("WaterMelts", "H2O", calib=False)


def _build_thermoengine_context(config: GeobarometerRunConfig) -> tuple[_ThermoEngineContext, dict[str, Any]]:
    if config.thermoengine_src_root is not None:
        os.environ["THERMOENGINE_SRC_ROOT"] = str(Path(config.thermoengine_src_root).expanduser().resolve())

    preflight = preflight_liam_runtime(strict=True)
    _ensure_thermoengine_importable()

    from thermoengine import core, equilibrate, model, phases  # noqa: PLC0415

    model_db = model.Database(liq_mod="v1.0")
    liquid = model_db.get_phase("Liq")
    feldspar = model_db.get_phase("Fsp")
    quartz = model_db.get_phase("Qz")
    spinel = model_db.get_phase("SplS")
    opx = model_db.get_phase("Opx")
    rhomox = _get_phase_compat(model_db, "Rhom", "Mag")
    water = _build_water_phase(model_db, phases)

    elm_liq_mat = liquid.props["element_comp"]
    idx_zero = np.argwhere(np.all(elm_liq_mat[..., :] == 0, axis=0))
    con_liq_mat = np.linalg.inv(np.delete(elm_liq_mat, idx_zero, axis=1))
    con_liq_mat[np.abs(con_liq_mat) < np.finfo(np.float64).eps] = 0

    con_phase_d: dict[str, np.ndarray] = {}
    for phase_obj in (quartz, feldspar, spinel, opx, rhomox):
        phase_name = str(phase_obj.props["phase_name"])
        elm_phase_mat = np.delete(phase_obj.props["element_comp"], idx_zero, axis=1)
        con_phase_mat = np.matmul(elm_phase_mat, con_liq_mat)
        con_phase_mat[np.abs(con_phase_mat) < 100 * np.finfo(np.float64).eps] = 0
        con_phase_d[phase_name] = con_phase_mat

    phase_objects = {
        "Quartz": quartz,
        "Feldspar": feldspar,
        "Spinel": spinel,
        "Opx": opx,
        "RhomOx": rhomox,
    }

    equil_liquid_water = equilibrate.Equilibrate(list(ELM_SYS), [liquid, water])
    equil_with_solids = equilibrate.Equilibrate(
        list(ELM_SYS),
        [liquid, feldspar, quartz, spinel, opx, rhomox, water],
    )

    ctx = _ThermoEngineContext(
        core=core,
        phases=phases,
        model=model,
        equilibrate=equilibrate,
        model_db=model_db,
        liquid=liquid,
        feldspar=feldspar,
        quartz=quartz,
        spinel=spinel,
        opx=opx,
        rhomox=rhomox,
        water=water,
        elm_sys=ELM_SYS,
        phase_objects=phase_objects,
        con_phase_d=con_phase_d,
        equil_liquid_water=equil_liquid_water,
        equil_with_solids=equil_with_solids,
    )
    return ctx, preflight


def _state_execute(
    equil_obj: Any,
    t_k: float,
    p_bar: float,
    blk_cmp: np.ndarray,
    f_o2_offset: float,
    cache: _StateCache,
) -> Any:
    if cache.state is None:
        cache.state = equil_obj.execute(
            t_k,
            p_bar,
            bulk_comp=blk_cmp,
            con_deltaNNO=f_o2_offset,
            debug=0,
            stats=False,
        )
    else:
        cache.state = equil_obj.execute(
            t_k,
            p_bar,
            state=cache.state,
            con_deltaNNO=f_o2_offset,
            debug=0,
            stats=False,
        )
    return cache.state


def _evaluate_affinity_result(
    ctx: _ThermoEngineContext,
    state: Any,
    t_k: float,
    p_bar: float,
) -> dict[str, tuple[float, Any]]:
    mu_liq = np.asarray(state.dGdn(t=t_k, p=p_bar, element_basis=False)[:, 0], dtype=float)
    mu_fld, mu_qtz = compute_mu_vectors_from_liquid_potentials(mu_liq)

    fld = ctx.feldspar.affinity_and_comp(t_k, p_bar, mu_fld, method="special")
    qtz = ctx.quartz.affinity_and_comp(t_k, p_bar, mu_qtz, method="special")
    return {
        "Feldspar": (float(fld[0]), fld[1]),
        "Quartz": (float(qtz[0]), qtz[1]),
    }


def _affinity_objective(
    tp: np.ndarray,
    ctx: _ThermoEngineContext,
    blk_cmp: np.ndarray,
    f_o2_offset: float,
    cache: _StateCache,
    return_details: bool = False,
) -> float | tuple[float, dict[str, tuple[float, Any]], Any]:
    t_k = float(tp[0])
    p_bar = float(tp[1])
    state = _state_execute(ctx.equil_liquid_water, t_k, p_bar, blk_cmp, f_o2_offset, cache)
    affinity_d = _evaluate_affinity_result(ctx, state, t_k, p_bar)
    objective = affinity_objective_from_affinities(
        affinity_d["Feldspar"][0],
        affinity_d["Quartz"][0],
    )
    if return_details:
        return objective, affinity_d, state
    return objective


def _saturation_curve_residual(
    t_k: float,
    p_bar: float,
    ctx: _ThermoEngineContext,
    blk_cmp: np.ndarray,
    f_o2_offset: float,
    phase_obj: Any,
    cache: _StateCache,
) -> float:
    state = _state_execute(ctx.equil_liquid_water, t_k, p_bar, blk_cmp, f_o2_offset, cache)
    mu_liq = np.asarray(state.dGdn(t=t_k, p=p_bar, element_basis=False)[:, 0], dtype=float)
    m_liq = np.asarray(state.compositions(phase_name="Liquid"), dtype=float)

    phase_name = str(phase_obj.props["phase_name"])
    con_phase_matrix = ctx.con_phase_d[phase_name]
    mu_phase = build_phase_mu_from_con_matrix(con_phase_matrix, mu_liq, m_liq)
    result = phase_obj.affinity_and_comp(t_k, p_bar, mu_phase, method="special")
    return float(result[0])


def _solve_geobarometer(
    ctx: _ThermoEngineContext,
    blk_cmp: np.ndarray,
    f_o2_offset: float,
    t_init_k: float,
    p_init_bar: float,
) -> dict[str, Any]:
    cache = _StateCache()
    result = opt.minimize(
        _affinity_objective,
        np.array([t_init_k, p_init_bar], dtype=float),
        args=(ctx, blk_cmp, f_o2_offset, cache),
        method="Nelder-Mead",
        options={"xatol": 1.0, "fatol": 1.0, "disp": False, "return_all": False},
    )

    verify_cache = _StateCache()
    objective, affinity_d, _state = _affinity_objective(
        np.array(result.x, dtype=float),
        ctx,
        blk_cmp,
        f_o2_offset,
        verify_cache,
        return_details=True,
    )

    return {
        "success": bool(result.success),
        "status": "success" if result.success else "optimizer_failed",
        "message": str(result.message),
        "T_K": float(result.x[0]),
        "T_C": float(result.x[0] - 273.15),
        "P_bar": float(result.x[1]),
        "P_MPa": float(result.x[1] / 10.0),
        "objective": float(objective),
        "objective_raw": float(result.fun),
        "iterations": int(getattr(result, "nit", 0)),
        "function_evals": int(getattr(result, "nfev", 0)),
        "affinities": {
            name: {
                "affinity": float(value[0]),
                "composition": _to_json_safe(value[1]),
            }
            for name, value in affinity_d.items()
        },
    }


def _find_sign_change_bracket(values: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    for i in range(len(values) - 1):
        p0, f0 = values[i]
        p1, f1 = values[i + 1]
        if f0 == 0:
            return (p0, p0)
        if f1 == 0:
            return (p1, p1)
        if f0 * f1 < 0:
            return (p0, p1)
    return None


def _solve_pressure_root_for_phase(
    ctx: _ThermoEngineContext,
    blk_cmp: np.ndarray,
    f_o2_offset: float,
    t_fixed_k: float,
    pressure_bracket_bar: tuple[float, float],
    phase_obj: Any,
) -> dict[str, Any]:
    p_lo, p_hi = sorted((float(pressure_bracket_bar[0]), float(pressure_bracket_bar[1])))
    phase_name = str(phase_obj.props["phase_name"])

    def residual_at_pressure(p_bar: float, cache: _StateCache) -> float:
        return _saturation_curve_residual(t_fixed_k, p_bar, ctx, blk_cmp, f_o2_offset, phase_obj, cache)

    scan_pressures = np.linspace(p_lo, p_hi, 41)
    scan_values: list[tuple[float, float]] = []
    cache = _StateCache()
    for p in scan_pressures:
        try:
            scan_values.append((float(p), float(residual_at_pressure(float(p), cache))))
        except Exception:
            scan_values.append((float(p), float("nan")))

    finite_scan = [(p, f) for p, f in scan_values if np.isfinite(f)]
    bracket = _find_sign_change_bracket(finite_scan) if len(finite_scan) >= 2 else None

    if bracket is None:
        return {
            "success": False,
            "status": "no_bracket_sign_change",
            "phase": phase_name,
            "T_K": float(t_fixed_k),
            "P_bar": np.nan,
            "P_MPa": np.nan,
            "residual": np.nan,
            "input_bracket_bar": (p_lo, p_hi),
            "effective_bracket_bar": None,
            "scan": [{"P_bar": p, "residual": f} for p, f in scan_values],
        }

    if bracket[0] == bracket[1]:
        p_root = float(bracket[0])
        res = float(next(f for p, f in finite_scan if p == p_root))
        return {
            "success": True,
            "status": "success",
            "phase": phase_name,
            "T_K": float(t_fixed_k),
            "P_bar": p_root,
            "P_MPa": p_root / 10.0,
            "residual": res,
            "input_bracket_bar": (p_lo, p_hi),
            "effective_bracket_bar": bracket,
            "scan": [{"P_bar": p, "residual": f} for p, f in scan_values],
        }

    root_cache = _StateCache()

    def residual_for_solver(p_bar: float) -> float:
        return residual_at_pressure(float(p_bar), root_cache)

    solver = opt.root_scalar(
        residual_for_solver,
        bracket=bracket,
        method="brentq",
        xtol=0.1,
    )
    p_root = float(solver.root)
    residual = float(residual_for_solver(p_root))
    return {
        "success": bool(solver.converged),
        "status": "success" if solver.converged else "solver_failed",
        "phase": phase_name,
        "T_K": float(t_fixed_k),
        "P_bar": p_root,
        "P_MPa": p_root / 10.0,
        "residual": residual,
        "input_bracket_bar": (p_lo, p_hi),
        "effective_bracket_bar": bracket,
        "scan": [{"P_bar": p, "residual": f} for p, f in scan_values],
    }


def _build_pressure_grid_mpa(start_mpa: float, stop_mpa: float, step_mpa: float) -> list[float]:
    if step_mpa <= 0:
        raise ValueError("Pressure step must be > 0 MPa.")

    values: list[float] = []
    if start_mpa >= stop_mpa:
        current = float(start_mpa)
        while current >= stop_mpa - 1e-9:
            values.append(float(current))
            current -= step_mpa
        if not values or abs(values[-1] - stop_mpa) > 1e-8:
            values.append(float(stop_mpa))
    else:
        current = float(start_mpa)
        while current <= stop_mpa + 1e-9:
            values.append(float(current))
            current += step_mpa
        if not values or abs(values[-1] - stop_mpa) > 1e-8:
            values.append(float(stop_mpa))
    return values


def _solve_temperature_root_for_phase(
    ctx: _ThermoEngineContext,
    blk_cmp: np.ndarray,
    f_o2_offset: float,
    phase_obj: Any,
    pressure_bar: float,
    t_guess_k: float,
    t_bracket_k: tuple[float, float],
    secant_step_k: float,
) -> dict[str, Any]:
    phase_name = str(phase_obj.props["phase_name"])

    def residual_t(t_k: float, cache: _StateCache) -> float:
        return _saturation_curve_residual(
            float(t_k),
            float(pressure_bar),
            ctx,
            blk_cmp,
            f_o2_offset,
            phase_obj,
            cache,
        )

    secant_cache = _StateCache()

    def secant_func(t_k: float) -> float:
        return residual_t(float(t_k), secant_cache)

    try:
        secant = opt.root_scalar(
            secant_func,
            x0=float(t_guess_k),
            x1=float(t_guess_k - secant_step_k),
            method="secant",
            xtol=0.1,
        )
        if secant.converged and np.isfinite(secant.root):
            root_t = float(secant.root)
            residual = float(secant_func(root_t))
            return {
                "success": True,
                "status": "success",
                "method": "secant",
                "phase_name": phase_name,
                "pressure_bar": float(pressure_bar),
                "pressure_MPa": float(pressure_bar / 10.0),
                "T_K": root_t,
                "T_C": root_t - 273.15,
                "residual": residual,
                "converged": True,
            }
    except Exception:
        pass

    t_lo, t_hi = sorted((float(t_bracket_k[0]), float(t_bracket_k[1])))
    scan_t = np.linspace(t_lo, t_hi, 51)
    scan: list[tuple[float, float]] = []
    scan_cache = _StateCache()
    for t in scan_t:
        try:
            scan.append((float(t), float(residual_t(float(t), scan_cache))))
        except Exception:
            scan.append((float(t), float("nan")))

    finite_scan = [(t, r) for t, r in scan if np.isfinite(r)]
    bracket = _find_sign_change_bracket(finite_scan) if len(finite_scan) >= 2 else None

    if bracket is None:
        if finite_scan:
            nearest = min(finite_scan, key=lambda item: abs(item[1]))
            t_fallback = float(nearest[0])
            residual = float(nearest[1])
        else:
            t_fallback = float("nan")
            residual = float("nan")
        return {
            "success": False,
            "status": "no_bracket_sign_change",
            "method": "scan",
            "phase_name": phase_name,
            "pressure_bar": float(pressure_bar),
            "pressure_MPa": float(pressure_bar / 10.0),
            "T_K": t_fallback,
            "T_C": t_fallback - 273.15 if np.isfinite(t_fallback) else np.nan,
            "residual": residual,
            "converged": False,
        }

    if bracket[0] == bracket[1]:
        t_root = float(bracket[0])
        residual = float(next(r for t, r in finite_scan if t == t_root))
        return {
            "success": True,
            "status": "success",
            "method": "scan-exact",
            "phase_name": phase_name,
            "pressure_bar": float(pressure_bar),
            "pressure_MPa": float(pressure_bar / 10.0),
            "T_K": t_root,
            "T_C": t_root - 273.15,
            "residual": residual,
            "converged": True,
        }

    brent_cache = _StateCache()

    def brent_func(t_k: float) -> float:
        return residual_t(float(t_k), brent_cache)

    brent = opt.root_scalar(brent_func, bracket=bracket, method="brentq", xtol=0.1)
    t_root = float(brent.root)
    residual = float(brent_func(t_root))
    return {
        "success": bool(brent.converged),
        "status": "success" if brent.converged else "solver_failed",
        "method": "brentq",
        "phase_name": phase_name,
        "pressure_bar": float(pressure_bar),
        "pressure_MPa": float(pressure_bar / 10.0),
        "T_K": t_root,
        "T_C": t_root - 273.15,
        "residual": residual,
        "converged": bool(brent.converged),
    }


def _run_liquidus_curves(
    ctx: _ThermoEngineContext,
    blk_cmp: np.ndarray,
    f_o2_offset: float,
    pressure_grid_mpa: Sequence[float],
    phase_keys: Sequence[str],
    t_seed_k: float,
    t_bracket_k: tuple[float, float],
    secant_step_k: float,
    sample_id: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for phase_key in phase_keys:
        phase_obj = ctx.phase_objects[phase_key]
        t_guess = float(t_seed_k)
        for p_mpa in pressure_grid_mpa:
            pressure_bar = float(p_mpa * 10.0)
            result = _solve_temperature_root_for_phase(
                ctx,
                blk_cmp,
                f_o2_offset,
                phase_obj,
                pressure_bar,
                t_guess,
                t_bracket_k,
                secant_step_k,
            )
            if result["success"] and np.isfinite(result["T_K"]):
                t_guess = float(result["T_K"])
            rows.append(
                {
                    "sample_id": sample_id,
                    "phase_key": phase_key,
                    "phase_name": result["phase_name"],
                    "pressure_bar": result["pressure_bar"],
                    "pressure_MPa": result["pressure_MPa"],
                    "T_K": result["T_K"],
                    "T_C": result["T_C"],
                    "residual": result["residual"],
                    "converged": bool(result["converged"]),
                    "status": result["status"],
                    "method": result["method"],
                }
            )
    return pd.DataFrame(rows)


def _compute_legacy_pressure_estimates(
    liquidus_df: pd.DataFrame,
    delta_residual_for_dp: float,
) -> dict[str, Any]:
    if liquidus_df.empty:
        return {
            "P2_MPa": np.nan,
            "dP2_MPa": np.nan,
            "P3_MPa": np.nan,
            "dP3_MPa": np.nan,
            "fit_P2": {"success": False, "reason": "no_liquidus_data"},
            "fit_P3": {"success": False, "reason": "no_liquidus_data"},
        }

    filtered = liquidus_df[(liquidus_df["converged"]) & (np.isfinite(liquidus_df["T_C"]))].copy()
    if filtered.empty:
        return {
            "P2_MPa": np.nan,
            "dP2_MPa": np.nan,
            "P3_MPa": np.nan,
            "dP3_MPa": np.nan,
            "fit_P2": {"success": False, "reason": "no_converged_temperatures"},
            "fit_P3": {"success": False, "reason": "no_converged_temperatures"},
        }

    records: list[dict[str, float]] = []
    for pressure_mpa, frame in filtered.groupby("pressure_MPa"):
        temps = np.sort(frame["T_C"].to_numpy(dtype=float))[::-1]
        delta_2 = float(temps[0] - temps[1]) if temps.size >= 2 else np.nan
        delta_3 = float(temps[0] - temps[2]) if temps.size >= 3 else np.nan
        records.append(
            {
                "pressure_MPa": float(pressure_mpa),
                "delta_2": delta_2,
                "delta_3": delta_3,
            }
        )

    residual_df = pd.DataFrame(records).sort_values("pressure_MPa")
    fit_p2 = fit_parabola_pressure_minimum(
        residual_df["pressure_MPa"].to_numpy(),
        residual_df["delta_2"].to_numpy(),
        delta_residual=delta_residual_for_dp,
    )
    fit_p3 = fit_parabola_pressure_minimum(
        residual_df["pressure_MPa"].to_numpy(),
        residual_df["delta_3"].to_numpy(),
        delta_residual=delta_residual_for_dp,
    )

    return {
        "P2_MPa": fit_p2.get("pressure_mpa", np.nan),
        "dP2_MPa": fit_p2.get("dP_mpa", np.nan),
        "P3_MPa": fit_p3.get("pressure_mpa", np.nan),
        "dP3_MPa": fit_p3.get("dP_mpa", np.nan),
        "fit_P2": fit_p2,
        "fit_P3": fit_p3,
        "residuals": residual_df,
    }


def _evaluate_solution_quality(
    ctx: _ThermoEngineContext,
    blk_cmp: np.ndarray,
    f_o2_offset: float,
    t_k: float,
    p_bar: float,
) -> dict[str, Any]:
    lw_cache = _StateCache()
    lw_state = _state_execute(ctx.equil_liquid_water, t_k, p_bar, blk_cmp, f_o2_offset, lw_cache)
    affinities = _evaluate_affinity_result(ctx, lw_state, t_k, p_bar)

    full_cache = _StateCache()
    full_state = _state_execute(ctx.equil_with_solids, t_k, p_bar, blk_cmp, f_o2_offset, full_cache)

    state_txt_buf = io.StringIO()
    with redirect_stdout(state_txt_buf):
        full_state.print_state()
    state_text = state_txt_buf.getvalue()

    properties = full_state.properties()
    stable_phases = sorted([name for name, status in properties.items() if status == "stable"])

    expected_stable = {
        "System",
        "Liquid",
        "Water",
        str(ctx.quartz.props["phase_name"]),
        str(ctx.feldspar.props["phase_name"]),
        str(ctx.spinel.props["phase_name"]),
        str(ctx.opx.props["phase_name"]),
        str(ctx.rhomox.props["phase_name"]),
    }
    unexpected = [name for name in stable_phases if name not in expected_stable]

    warnings: list[str] = []
    if unexpected:
        warnings.append(
            "Unexpected stable phases at solution: " + ", ".join(unexpected)
        )

    return {
        "stable_phases": stable_phases,
        "unexpected_stable_phases": unexpected,
        "warnings": warnings,
        "affinities": {
            name: {
                "affinity": float(values[0]),
                "composition": _to_json_safe(values[1]),
            }
            for name, values in affinities.items()
        },
        "state_text": state_text,
    }


def _run_benchmark_gate(ctx: _ThermoEngineContext, config: GeobarometerRunConfig) -> dict[str, Any]:
    blk_cmp = convert_oxide_wt_to_blk_cmp(CANONICAL_BENCHMARK_OXIDES, ctx.core, ctx.liquid, ELM_SYS)
    geobarometer = _solve_geobarometer(ctx, blk_cmp, 0.0, 1034.0, 1750.0)

    quartz_obj = ctx.phase_objects["Quartz"]
    quartz_root = _solve_temperature_root_for_phase(
        ctx,
        blk_cmp,
        0.0,
        quartz_obj,
        pressure_bar=3000.0,
        t_guess_k=1034.0,
        t_bracket_k=config.temperature_bracket_k,
        secant_step_k=config.temperature_secant_step_k,
    )

    objective_ratio = (
        geobarometer["objective"] / config.benchmark_baseline_objective
        if config.benchmark_baseline_objective > 0
        else float("inf")
    )

    checks = {
        "temperature": abs(geobarometer["T_C"] - config.benchmark_baseline_t_c) <= config.benchmark_tolerance_c,
        "pressure": abs(geobarometer["P_MPa"] - config.benchmark_baseline_p_mpa) <= config.benchmark_tolerance_mpa,
        "objective": objective_ratio <= config.benchmark_objective_ratio,
        "quartz_liquidus": abs(quartz_root["T_C"] - config.benchmark_baseline_quartz_3000bar_t_c)
        <= config.benchmark_quartz_tolerance_c,
    }

    return {
        "passed": all(checks.values()),
        "checks": checks,
        "geobarometer": geobarometer,
        "quartz_3000bar": quartz_root,
        "objective_ratio": float(objective_ratio),
        "expected": {
            "T_C": config.benchmark_baseline_t_c,
            "P_MPa": config.benchmark_baseline_p_mpa,
            "objective": config.benchmark_baseline_objective,
            "quartz_3000bar_T_C": config.benchmark_baseline_quartz_3000bar_t_c,
        },
        "tolerances": {
            "T_C": config.benchmark_tolerance_c,
            "P_MPa": config.benchmark_tolerance_mpa,
            "objective_ratio": config.benchmark_objective_ratio,
            "quartz_3000bar_T_C": config.benchmark_quartz_tolerance_c,
        },
    }


def _normalize_mode(mode: str) -> str:
    lowered = str(mode).strip().lower()
    valid = {"geobarometer", "pressure", "liquidus", "all"}
    if lowered not in valid:
        raise ValueError(f"Invalid mode '{mode}'. Use one of: {', '.join(sorted(valid))}")
    return lowered


def _write_dataframe_csv_xlsx(df: pd.DataFrame, csv_path: Path, xlsx_path: Path, sheet_name: str) -> None:
    df.to_csv(csv_path, index=False)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)


def run_geobarometer_driver(config: GeobarometerRunConfig) -> dict[str, Any]:
    """Execute single-driver ThermoEngine geobarometer pipeline."""
    mode = _normalize_mode(config.mode)
    run_pressure = mode in {"pressure", "all"}
    run_geobarometer = mode in {"geobarometer", "all"}
    run_liquidus = mode in {"liquidus", "all"}

    if run_pressure and config.pressure_bracket_bar is None:
        raise ValueError("pressure_bracket_bar is required for pressure and all modes.")

    sample_inputs = load_sample_inputs_from_csv(config.input_csv)
    if not sample_inputs:
        raise ValueError("No samples found in input CSV.")

    output_root = Path(config.output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_geobarometer")
    run_dir.mkdir(parents=True, exist_ok=True)

    if config.thermoengine_src_root is not None:
        os.environ["THERMOENGINE_SRC_ROOT"] = str(Path(config.thermoengine_src_root).expanduser().resolve())

    ctx, preflight = _build_thermoengine_context(config)

    benchmark = None
    if config.benchmark_first:
        benchmark = _run_benchmark_gate(ctx, config)
        benchmark_path = run_dir / "benchmark.json"
        benchmark_path.write_text(json.dumps(_to_json_safe(benchmark), indent=2), encoding="utf-8")
        if not benchmark["passed"]:
            raise RuntimeError(
                "Benchmark gate failed. "
                f"See {benchmark_path} for details before batch processing."
            )

    machine_rows: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    liquidus_frames: list[pd.DataFrame] = []
    sample_diagnostics: list[dict[str, Any]] = []

    snapshots_dir = run_dir / "state_snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    for sample in sample_inputs:
        sample_diag: dict[str, Any] = {
            "sample_id": sample.sample_id,
            "status": "success",
            "warnings": [],
            "errors": [],
            "geobarometer": None,
            "pressure_quartz": None,
            "pressure_feldspar": None,
            "legacy": None,
            "quality": None,
        }

        geobarometer_result: dict[str, Any] | None = None
        pressure_result_quartz: dict[str, Any] | None = None
        pressure_result_feldspar: dict[str, Any] | None = None
        liquidus_df = pd.DataFrame()
        legacy_estimates: dict[str, Any] = {
            "P2_MPa": np.nan,
            "dP2_MPa": np.nan,
            "P3_MPa": np.nan,
            "dP3_MPa": np.nan,
        }

        try:
            blk_cmp = convert_oxide_wt_to_blk_cmp(sample.oxides_wt, ctx.core, ctx.liquid, ELM_SYS)

            if run_geobarometer:
                geobarometer_result = _solve_geobarometer(
                    ctx,
                    blk_cmp,
                    sample.fO2_offset,
                    sample.T_init_K,
                    sample.P_init_bar,
                )
                sample_diag["geobarometer"] = geobarometer_result

            if run_pressure:
                t_fixed_k = (
                    geobarometer_result["T_K"]
                    if geobarometer_result is not None and geobarometer_result.get("success", False)
                    else sample.T_init_K
                )
                pressure_result_quartz = _solve_pressure_root_for_phase(
                    ctx,
                    blk_cmp,
                    sample.fO2_offset,
                    t_fixed_k,
                    config.pressure_bracket_bar,
                    ctx.phase_objects["Quartz"],
                )
                pressure_result_feldspar = _solve_pressure_root_for_phase(
                    ctx,
                    blk_cmp,
                    sample.fO2_offset,
                    t_fixed_k,
                    config.pressure_bracket_bar,
                    ctx.phase_objects["Feldspar"],
                )
                sample_diag["pressure_quartz"] = pressure_result_quartz
                sample_diag["pressure_feldspar"] = pressure_result_feldspar
                if not pressure_result_quartz.get("success", False):
                    sample_diag["warnings"].append(
                        f"Quartz pressure root failed with status={pressure_result_quartz.get('status')}"
                    )

            if run_liquidus:
                if config.pressure_grid_mpa is not None:
                    start_mpa, stop_mpa, step_mpa = config.pressure_grid_mpa
                else:
                    start_mpa, stop_mpa, step_mpa = sample.P0_MPa, sample.Pf_MPa, sample.dP_MPa
                pressure_grid_mpa = _build_pressure_grid_mpa(start_mpa, stop_mpa, step_mpa)

                t_seed_k = (
                    geobarometer_result["T_K"]
                    if geobarometer_result is not None and geobarometer_result.get("success", False)
                    else sample.T_init_K
                )
                liquidus_df = _run_liquidus_curves(
                    ctx,
                    blk_cmp,
                    sample.fO2_offset,
                    pressure_grid_mpa,
                    config.phase_keys,
                    t_seed_k,
                    config.temperature_bracket_k,
                    config.temperature_secant_step_k,
                    sample.sample_id,
                )
                liquidus_frames.append(liquidus_df)
                legacy_estimates = _compute_legacy_pressure_estimates(
                    liquidus_df,
                    config.delta_residual_for_dp,
                )
                sample_diag["legacy"] = {
                    "P2_MPa": legacy_estimates.get("P2_MPa"),
                    "dP2_MPa": legacy_estimates.get("dP2_MPa"),
                    "P3_MPa": legacy_estimates.get("P3_MPa"),
                    "dP3_MPa": legacy_estimates.get("dP3_MPa"),
                    "fit_P2": _to_json_safe(legacy_estimates.get("fit_P2")),
                    "fit_P3": _to_json_safe(legacy_estimates.get("fit_P3")),
                }

            quality_target_t = None
            quality_target_p = None
            if geobarometer_result is not None and geobarometer_result.get("success", False):
                quality_target_t = geobarometer_result["T_K"]
                quality_target_p = geobarometer_result["P_bar"]
            elif pressure_result_quartz is not None and pressure_result_quartz.get("success", False):
                quality_target_t = pressure_result_quartz["T_K"]
                quality_target_p = pressure_result_quartz["P_bar"]

            if quality_target_t is not None and quality_target_p is not None:
                quality = _evaluate_solution_quality(
                    ctx,
                    blk_cmp,
                    sample.fO2_offset,
                    float(quality_target_t),
                    float(quality_target_p),
                )
                sample_diag["quality"] = {
                    "stable_phases": quality["stable_phases"],
                    "unexpected_stable_phases": quality["unexpected_stable_phases"],
                    "warnings": quality["warnings"],
                    "affinities": quality["affinities"],
                }
                sample_diag["warnings"].extend(quality["warnings"])
                snapshot_path = snapshots_dir / f"{sample.sample_id}.txt"
                snapshot_path.write_text(quality["state_text"], encoding="utf-8")
                sample_diag["state_snapshot"] = str(snapshot_path)

        except Exception as exc:  # pragma: no cover - integration/runtime dependent
            sample_diag["status"] = "error"
            sample_diag["errors"].append(str(exc))

        sample_diagnostics.append(sample_diag)

        machine_rows.append(
            {
                "sample_id": sample.sample_id,
                "status": sample_diag["status"],
                "fO2_offset": sample.fO2_offset,
                "geobarometer_T_K": geobarometer_result.get("T_K") if geobarometer_result else np.nan,
                "geobarometer_T_C": geobarometer_result.get("T_C") if geobarometer_result else np.nan,
                "geobarometer_P_bar": geobarometer_result.get("P_bar") if geobarometer_result else np.nan,
                "geobarometer_P_MPa": geobarometer_result.get("P_MPa") if geobarometer_result else np.nan,
                "geobarometer_objective": geobarometer_result.get("objective") if geobarometer_result else np.nan,
                "pressure_quartz_status": pressure_result_quartz.get("status") if pressure_result_quartz else None,
                "pressure_quartz_P_bar": pressure_result_quartz.get("P_bar") if pressure_result_quartz else np.nan,
                "pressure_quartz_P_MPa": pressure_result_quartz.get("P_MPa") if pressure_result_quartz else np.nan,
                "pressure_quartz_residual": pressure_result_quartz.get("residual") if pressure_result_quartz else np.nan,
                "pressure_feldspar_status": pressure_result_feldspar.get("status") if pressure_result_feldspar else None,
                "pressure_feldspar_P_bar": pressure_result_feldspar.get("P_bar") if pressure_result_feldspar else np.nan,
                "pressure_feldspar_P_MPa": pressure_result_feldspar.get("P_MPa") if pressure_result_feldspar else np.nan,
                "pressure_feldspar_residual": pressure_result_feldspar.get("residual") if pressure_result_feldspar else np.nan,
                "legacy_P2_MPa": legacy_estimates.get("P2_MPa", np.nan),
                "legacy_dP2_MPa": legacy_estimates.get("dP2_MPa", np.nan),
                "legacy_P3_MPa": legacy_estimates.get("P3_MPa", np.nan),
                "legacy_dP3_MPa": legacy_estimates.get("dP3_MPa", np.nan),
                "warnings": " | ".join(sample_diag["warnings"]),
                "errors": " | ".join(sample_diag["errors"]),
            }
        )

        legacy_rows.append(
            {
                "date": sample.date,
                "sample ID": sample.sample_id,
                "P3": legacy_estimates.get("P3_MPa", np.nan),
                "dP3": legacy_estimates.get("dP3_MPa", np.nan),
                "P2": legacy_estimates.get("P2_MPa", np.nan),
                "dP2": legacy_estimates.get("dP2_MPa", np.nan),
                "dT": sample.dT_C,
                "dP": sample.dP_MPa,
                "H2O wt. %": sample.oxides_wt["H2O"],
                "T0": sample.T0_C,
                "Tf": sample.Tf_C,
                "P0": sample.P0_MPa,
                "Pf": sample.Pf_MPa,
                "plumi": sample.plumi,
                "Notes": sample.notes,
            }
        )

    machine_df = pd.DataFrame(machine_rows)
    legacy_df = pd.DataFrame(
        legacy_rows,
        columns=[
            "date",
            "sample ID",
            "P3",
            "dP3",
            "P2",
            "dP2",
            "dT",
            "dP",
            "H2O wt. %",
            "T0",
            "Tf",
            "P0",
            "Pf",
            "plumi",
            "Notes",
        ],
    )
    liquidus_df_all = pd.concat(liquidus_frames, ignore_index=True) if liquidus_frames else pd.DataFrame()

    summary_legacy_csv = run_dir / "summary_legacy.csv"
    summary_legacy_xlsx = run_dir / "summary_legacy.xlsx"
    _write_dataframe_csv_xlsx(legacy_df, summary_legacy_csv, summary_legacy_xlsx, "summary_legacy")

    summary_machine_csv = run_dir / "summary_machine.csv"
    summary_machine_xlsx = run_dir / "summary_machine.xlsx"
    _write_dataframe_csv_xlsx(machine_df, summary_machine_csv, summary_machine_xlsx, "summary_machine")

    liquidus_csv = run_dir / "liquidus_curves.csv"
    liquidus_xlsx = run_dir / "liquidus_curves.xlsx"
    if liquidus_df_all.empty:
        liquidus_df_all = pd.DataFrame(
            columns=[
                "sample_id",
                "phase_key",
                "phase_name",
                "pressure_bar",
                "pressure_MPa",
                "T_K",
                "T_C",
                "residual",
                "converged",
                "status",
                "method",
            ]
        )
    _write_dataframe_csv_xlsx(liquidus_df_all, liquidus_csv, liquidus_xlsx, "liquidus_curves")

    diagnostics_path = run_dir / "diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(_to_json_safe(sample_diagnostics), indent=2),
        encoding="utf-8",
    )

    output_paths = {
        "summary_legacy_csv": str(summary_legacy_csv),
        "summary_legacy_xlsx": str(summary_legacy_xlsx),
        "summary_machine_csv": str(summary_machine_csv),
        "summary_machine_xlsx": str(summary_machine_xlsx),
        "liquidus_curves_csv": str(liquidus_csv),
        "liquidus_curves_xlsx": str(liquidus_xlsx),
        "diagnostics_json": str(diagnostics_path),
    }
    if benchmark is not None:
        output_paths["benchmark_json"] = str(run_dir / "benchmark.json")

    failed_count = int((machine_df["status"] == "error").sum())
    successful_count = int(len(machine_df) - failed_count)

    return {
        "run_dir": str(run_dir),
        "mode": mode,
        "sample_count": len(sample_inputs),
        "successful_count": successful_count,
        "failed_count": failed_count,
        "benchmark": _to_json_safe(benchmark) if benchmark is not None else None,
        "preflight": _to_json_safe(preflight),
        "output_paths": output_paths,
        "samples": _to_json_safe(sample_diagnostics),
    }


__all__ = [
    "ELM_SYS",
    "REQUIRED_OXIDES",
    "DEFAULT_PHASE_KEYS",
    "GeobarometerSampleInput",
    "GeobarometerRunConfig",
    "load_sample_inputs_from_csv",
    "convert_oxide_wt_to_blk_cmp",
    "compute_mu_vectors_from_liquid_potentials",
    "affinity_objective_from_affinities",
    "build_phase_mu_from_con_matrix",
    "fit_parabola_pressure_minimum",
    "run_geobarometer_driver",
]
