from __future__ import annotations

import csv
import importlib.util
import json
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import openpyxl


LIAM_WORKBOOK = Path("/Users/lopezama/Downloads/Parallel_MELTS_KCP-109C_dP25.0_02-25_8cores.xlsx")
LIAM_HELPER = Path("/Users/lopezama/Downloads/MeltsHelperFunctions.py")
OUTPUT_ROOT = Path("/Users/lopezama/PycharmProjects/sci-cluster/review_outputs/rmelts_rootcause_matmul")


@dataclass
class ProbeTrace:
    pressure_mpa: float
    temperature_range_c: str
    mode: str
    outcome: str
    liquidus_temp_c: float | None
    elapsed_s: float
    exception_type: str | None
    exception_message: str | None
    traceback_text: str | None


@dataclass
class LiquidusCallTrace:
    pressure_mpa: float
    mode: str
    exec_mode: str
    iteration_index: int
    stage: str
    target_temp_c: float
    state_reused: bool
    outcome: str
    elapsed_s: float
    exception_type: str | None
    exception_message: str | None


@dataclass
class LiquidusModeSummary:
    pressure_mpa: float
    mode: str
    exec_mode: str
    outcome: str
    liquidus_temp_c: float | None
    elapsed_s: float
    iterations_completed: int
    exception_type: str | None
    exception_message: str | None
    traceback_text: str | None


def _now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


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
        return value.item()  # numpy scalars
    except Exception:
        return str(value)


def _load_liam_helper(helper_path: Path):
    helper_dir = str(helper_path.parent)
    if helper_dir not in sys.path:
        sys.path.insert(0, helper_dir)
    module_name = f"MeltsHelperFunctions_liquidus_diag_{int(time.time())}"
    spec = importlib.util.spec_from_file_location(module_name, str(helper_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import helper from {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _read_liam_init_cond(workbook_path: Path) -> tuple[dict[str, float], dict[str, Any]]:
    wb = openpyxl.load_workbook(workbook_path, data_only=True, read_only=True)
    try:
        ws = wb["init_cond"]
        composition: dict[str, float] = {}
        params: dict[str, Any] = {}

        for row in ws.iter_rows(min_row=1, max_col=8, values_only=True):
            oxide = row[0]
            oxide_val = row[1]
            param_name = row[3]
            param_val = row[4]

            if isinstance(oxide, str) and oxide.strip():
                if oxide_val is not None:
                    composition[str(oxide).strip()] = float(oxide_val)

            if isinstance(param_name, str) and param_name.strip():
                params[str(param_name).strip()] = param_val
        return composition, params
    finally:
        wb.close()


def _build_blk_cmp_from_liam_composition(composition_dict: dict[str, float], t1_c: float, p_mpa: float, fo2_offset: float):
    import numpy as np
    from thermoengine import core, model, phases, equilibrate

    model_db = model.Database(liq_mod="v1.0")
    liquid = model_db.get_phase("Liq")
    feldspar = model_db.get_phase("Fsp")
    quartz = model_db.get_phase("Qz")
    spinel = model_db.get_phase("SplS")
    opx = model_db.get_phase("Opx")
    rhom_ox = model_db.get_phase("Rhom")
    water = phases.PurePhase("WaterMelts", "H2O", calib=False)

    elm_sys_local = ["H", "O", "Na", "Mg", "Al", "Si", "P", "K", "Ca", "Ti", "Cr", "Mn", "Fe", "Co", "Ni"]
    phs_sys_local = [liquid, feldspar, water, quartz, spinel, opx, rhom_ox]

    # Match Liam's composition-prep path in parallel_melts_main_loop first.
    mol_oxides = core.chem.format_mol_oxide_comp(composition_dict, convert_grams_to_moles=True)
    moles_end_seed, _oxide_res_seed = liquid.calc_endmember_comp(
        mol_oxide_comp=mol_oxides,
        method="intrinsic",
        output_residual=True,
    )
    if not liquid.test_endmember_comp(moles_end_seed):
        raise RuntimeError("Initial endmember composition is infeasible during diagnostic setup")
    mol_elm_seed = liquid.convert_endmember_comp(moles_end_seed, output="moles_elements")
    blk_cmp_seed = np.array([mol_elm_seed[core.chem.PERIODIC_ORDER.tolist().index(elm)] for elm in elm_sys_local])

    # Match Liam's worker-side reinitialization path in run_single_pressure_step.
    equil = equilibrate.Equilibrate(elm_sys_local, phs_sys_local)
    state = equilibrate.EquilState(equil.element_list, equil.phase_list)
    omni_phase = state.omni_phase()
    state.set_phase_comp(omni_phase, blk_cmp_seed, input_as_elements=True)

    ox = equil.kc_ferric_ferrous(
        t1_c + 273.15,
        p_mpa * 10.0,
        state.phase_d["Liquid"]["moles"],
        compute="oxides",
        deltaNNO=fo2_offset,
        debug=0,
    )
    moles_end, _oxide_res = liquid.calc_endmember_comp(mol_oxide_comp=ox, method="intrinsic", output_residual=True)
    if not liquid.test_endmember_comp(moles_end):
        raise RuntimeError("Calculated endmember composition is infeasible during diagnostic setup")
    mol_elm = liquid.convert_endmember_comp(moles_end, output="moles_elements")
    blk_cmp = np.array([mol_elm[core.chem.PERIODIC_ORDER.tolist().index(elm)] for elm in elm_sys_local])
    return equil, blk_cmp


def _write_trace_csv(path: Path, traces: list[ProbeTrace]) -> None:
    rows = [asdict(t) for t in traces]
    if not rows:
        rows = [asdict(ProbeTrace(0.0, "", "", "", None, 0.0, None, None, None))]
        rows = []
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "pressure_mpa",
                "temperature_range_c",
                "mode",
                "outcome",
                "liquidus_temp_c",
                "elapsed_s",
                "exception_type",
                "exception_message",
                "traceback_text",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_liquidus_call_trace_csv(path: Path, traces: list[LiquidusCallTrace]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "pressure_mpa",
                "mode",
                "exec_mode",
                "iteration_index",
                "stage",
                "target_temp_c",
                "state_reused",
                "outcome",
                "elapsed_s",
                "exception_type",
                "exception_message",
            ],
        )
        writer.writeheader()
        for row in [_json_safe(asdict(t)) for t in traces]:
            writer.writerow(row)


def _stable_phases(state: Any) -> list[str]:
    phases = state.properties()
    stable = [name for name, status in phases.items() if status == "stable"]
    if "System" in stable:
        stable.remove("System")
    return stable


def _diag_safe_exec(
    helper: Any,
    *,
    equil: Any,
    temp_c: float,
    pressure_mpa: float,
    composition: Any,
    fO2_offset: float,
    timeout_s: float,
    dbg: int,
    prior_state: Any,
    reuse_state: bool,
    call_traces: list[LiquidusCallTrace],
    mode_name: str,
    exec_mode: str,
    iteration_index: int,
    stage: str,
):
    kwargs = {"con_deltaNNO": float(fO2_offset), "debug": int(dbg)}
    if reuse_state and prior_state is not None:
        kwargs["state"] = prior_state
    else:
        kwargs["bulk_comp"] = composition
    t0 = time.time()
    try:
        if exec_mode == "thread_timeout_wrapper":
            state = helper.safe_equilibrium_execute(
                equil,
                float(temp_c) + 273.15,
                float(pressure_mpa) * 10.0,
                timeout=float(timeout_s),
                **kwargs,
            )
        elif exec_mode == "raw_execute":
            state = equil.execute(
                float(temp_c) + 273.15,
                float(pressure_mpa) * 10.0,
                **kwargs,
            )
        else:
            raise ValueError(f"Unknown exec_mode: {exec_mode}")
        elapsed = time.time() - t0
        if state is None:
            call_traces.append(
                LiquidusCallTrace(
                    pressure_mpa=float(pressure_mpa),
                    mode=mode_name,
                    exec_mode=exec_mode,
                    iteration_index=int(iteration_index),
                    stage=stage,
                    target_temp_c=float(temp_c),
                    state_reused=bool(reuse_state and prior_state is not None),
                    outcome="none",
                    elapsed_s=float(elapsed),
                    exception_type=None,
                    exception_message=None,
                )
            )
            return None
        call_traces.append(
            LiquidusCallTrace(
                pressure_mpa=float(pressure_mpa),
                mode=mode_name,
                exec_mode=exec_mode,
                iteration_index=int(iteration_index),
                stage=stage,
                target_temp_c=float(temp_c),
                state_reused=bool(reuse_state and prior_state is not None),
                outcome="success",
                elapsed_s=float(elapsed),
                exception_type=None,
                exception_message=None,
            )
        )
        return state
    except Exception as exc:
        elapsed = time.time() - t0
        call_traces.append(
            LiquidusCallTrace(
                pressure_mpa=float(pressure_mpa),
                mode=mode_name,
                exec_mode=exec_mode,
                iteration_index=int(iteration_index),
                stage=stage,
                target_temp_c=float(temp_c),
                state_reused=bool(reuse_state and prior_state is not None),
                outcome="exception",
                elapsed_s=float(elapsed),
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
        )
        raise


def _find_wet_liquidus_state_reuse_diagnostic(
    helper: Any,
    *,
    equil: Any,
    T1: float,
    T2: float,
    pressure_mpa: float,
    composition: Any,
    fO2_offset: float,
    mode_name: str,
    call_traces: list[LiquidusCallTrace],
    exec_mode: str = "thread_timeout_wrapper",
    trace_mode_label: str | None = None,
    rebuild_equil_comp_fn: Any | None = None,
    reset_equil_on_none: bool = False,
) -> tuple[float | None, int, str | None, str | None, str | None]:
    """
    Diagnostic clone of Liam's find_wet_liquidus that allows controlled state-reuse modes.
    Returns (liquidus_temp, iterations_completed, exception_type, exception_message, traceback_text)
    """
    import traceback as _tb

    if T1 < T2:
        T1, T2 = T2, T1
    current_T = round((T1 + T2) / 2)
    i = 0
    timeout = 10
    dbg = 0
    max_outer_iterations = 200
    max_consecutive_none = 5
    max_temp_c = float(max(T1, T2)) + 250.0
    min_temp_c = float(min(T1, T2)) - 250.0

    mode_cfg = {
        "reuse_state": {"main": True, "probe": True, "reset_after_exception": False},
        "fresh_state_each_iteration": {"main": False, "probe": False, "reset_after_exception": False},
        "fresh_state_after_exception": {"main": True, "probe": True, "reset_after_exception": True},
        "fresh_state_probe_only": {"main": True, "probe": False, "reset_after_exception": False},
    }
    if mode_name not in mode_cfg:
        raise ValueError(f"Unknown state-reuse diagnostic mode: {mode_name}")
    cfg = mode_cfg[mode_name]
    state = None
    consecutive_none_main = 0
    trace_mode = trace_mode_label or mode_name

    def _handle_exception(exc: Exception):
        nonlocal state
        if cfg["reset_after_exception"]:
            state = None
        return type(exc).__name__, str(exc), _tb.format_exc()

    try:
        state = _diag_safe_exec(
            helper,
            equil=equil,
            temp_c=current_T,
            pressure_mpa=pressure_mpa,
            composition=composition,
            fO2_offset=fO2_offset,
            timeout_s=timeout,
            dbg=dbg,
            prior_state=state,
            reuse_state=False,
            call_traces=call_traces,
            mode_name=trace_mode,
            exec_mode=exec_mode,
            iteration_index=-1,
            stage="initial_mid",
        )
        if state is None:
            return float(T1), i, None, "Initial equilibrium calculation returned None", None

        phases = _stable_phases(state)
        min_phases = 2 if "Water" in phases else 1

        if len(phases) > min_phases:
            state_probe = _diag_safe_exec(
                helper,
                equil=equil,
                temp_c=current_T + 1,
                pressure_mpa=pressure_mpa,
                composition=composition,
                fO2_offset=fO2_offset,
                timeout_s=timeout,
                dbg=dbg,
                prior_state=state,
                reuse_state=bool(cfg["probe"]),
                call_traces=call_traces,
                mode_name=trace_mode,
                exec_mode=exec_mode,
                iteration_index=-1,
                stage="initial_probe",
            )
            if state_probe is None:
                return float(T1), i, None, "Initial probe equilibrium calculation returned None", None
            if cfg["probe"]:
                state = state_probe
            phases_probe = _stable_phases(state_probe)
            if len(phases_probe) == min_phases:
                return float(current_T), i, None, None, None
            temp = current_T + 1
            current_T = round((temp + T1) / 2)
            T2 = temp
        else:
            temp = current_T - 1
            current_T = round((temp + T2) / 2)
            T1 = temp

        while (T1 > T2) and i < max_outer_iterations:
            if (current_T > max_temp_c) or (current_T < min_temp_c):
                return (
                    float(T1),
                    i,
                    "RunawayTemperatureError",
                    (
                        f"current_T={current_T}C outside diagnostic bounds "
                        f"[{min_temp_c}, {max_temp_c}]"
                    ),
                    None,
                )
            try:
                state_main = _diag_safe_exec(
                    helper,
                    equil=equil,
                    temp_c=current_T,
                    pressure_mpa=pressure_mpa,
                    composition=composition,
                    fO2_offset=fO2_offset,
                    timeout_s=timeout,
                    dbg=dbg,
                    prior_state=state,
                    reuse_state=bool(cfg["main"]),
                    call_traces=call_traces,
                    mode_name=trace_mode,
                    exec_mode=exec_mode,
                    iteration_index=i,
                    stage="main",
                )
                if state_main is None:
                    if reset_equil_on_none and rebuild_equil_comp_fn is not None:
                        try:
                            equil, composition = rebuild_equil_comp_fn()
                            state = None
                        except Exception:
                            pass
                    consecutive_none_main += 1
                    i += 1
                    if consecutive_none_main >= max_consecutive_none:
                        return (
                            float(T1),
                            i,
                            "RepeatedNoneStateError",
                            (
                                f"main equilibrium returned None {consecutive_none_main} "
                                f"times in a row at/near T={current_T}C"
                            ),
                            None,
                        )
                    current_T = current_T + 25
                    continue
                consecutive_none_main = 0

                if cfg["main"]:
                    state = state_main
                phases = _stable_phases(state_main)
                min_phases = 2 if "Water" in phases else 1

                if len(phases) > min_phases:
                    probe_prior = state_main if cfg["main"] else state
                    state_probe = _diag_safe_exec(
                        helper,
                        equil=equil,
                        temp_c=current_T + 1,
                        pressure_mpa=pressure_mpa,
                        composition=composition,
                        fO2_offset=fO2_offset,
                        timeout_s=timeout,
                        dbg=dbg,
                        prior_state=probe_prior,
                        reuse_state=bool(cfg["probe"]),
                        call_traces=call_traces,
                        mode_name=trace_mode,
                        exec_mode=exec_mode,
                        iteration_index=i,
                        stage="probe",
                    )
                    if state_probe is None:
                        if reset_equil_on_none and rebuild_equil_comp_fn is not None:
                            try:
                                equil, composition = rebuild_equil_comp_fn()
                                state = None
                            except Exception:
                                pass
                        return float(T1), i, None, "Probe equilibrium calculation returned None", None
                    if cfg["probe"]:
                        state = state_probe
                    phases_probe = _stable_phases(state_probe)
                    if len(phases_probe) == min_phases:
                        return float(current_T + 1), i + 1, None, None, None
                    temp = current_T + 1
                    current_T = round((temp + T1) / 2)
                    T2 = temp
                else:
                    temp = current_T - 1
                    current_T = round((temp + T2) / 2)
                    T1 = temp
                i += 1
            except Exception as exc:
                etype, emsg, tb = _handle_exception(exc)
                return float(T1), i, etype, emsg, tb
        return float(T1), i, None, None, None
    except Exception as exc:
        etype, emsg, tb = _handle_exception(exc)
        return None, i, etype, emsg, tb


def main() -> int:
    run_tag = _now_tag()
    run_root = _ensure_dir(OUTPUT_ROOT / f"kcp109c_liquidus_rootcause_{run_tag}")
    phase1_dir = _ensure_dir(run_root / "phase1_min_reproducer")
    workflow_notes_path = run_root / "workflow_notes.log"
    trace_csv_path = phase1_dir / "trace.csv"
    trace_json_path = phase1_dir / "trace.json"
    summary_json_path = run_root / "diagnosis_summary.json"
    phase3_dir = _ensure_dir(run_root / "phase3_state_reuse_ab")
    phase4_dir = _ensure_dir(run_root / "phase4_timeout_ab")

    pressures = [50.0, 75.0, 100.0]
    notes: list[str] = []
    notes.append(f"run_tag={run_tag}")
    notes.append(f"liam_workbook={LIAM_WORKBOOK}")
    notes.append(f"liam_helper={LIAM_HELPER}")
    notes.append("phase=1 minimal liquidus reproducer")
    notes.append("mode=liam_find_wet_liquidus_direct")

    helper = _load_liam_helper(LIAM_HELPER)
    composition_dict, init_params = _read_liam_init_cond(LIAM_WORKBOOK)
    t1 = float(init_params.get("T1 (C)", 1100))
    t2 = float(init_params.get("T2 (C)", 700))
    fo2_offset = float(init_params.get("fO2 value", 0.0))
    fo2_buffer = str(init_params.get("fO2 buffer", "NNO"))
    notes.append(f"init_cond_T={t1}->{t2}C")
    notes.append(f"init_cond_fO2_offset={fo2_offset}")
    notes.append(f"init_cond_fO2_buffer={fo2_buffer}")
    notes.append(f"probe_pressures_mpa={pressures}")
    notes.append(f"composition_keys={sorted(composition_dict.keys())}")

    traces: list[ProbeTrace] = []
    for pressure in pressures:
        t_start = time.time()
        try:
            equil, blk_cmp = _build_blk_cmp_from_liam_composition(composition_dict, t1_c=1200.0, p_mpa=pressure, fo2_offset=fo2_offset)
            liquidus_temp = helper.find_wet_liquidus(
                equil,
                1200.0,
                700.0,
                float(pressure),
                50,
                blk_cmp,
                float(fo2_offset),
                False,
            )
            elapsed = time.time() - t_start
            trace = ProbeTrace(
                pressure_mpa=float(pressure),
                temperature_range_c="1200->700",
                mode="liam_find_wet_liquidus_direct",
                outcome="success",
                liquidus_temp_c=float(liquidus_temp) if liquidus_temp is not None else None,
                elapsed_s=float(elapsed),
                exception_type=None,
                exception_message=None,
                traceback_text=None,
            )
            print(
                f"phase1 pressure={pressure:.1f}MPa outcome=success liquidus_temp_c={trace.liquidus_temp_c} elapsed_s={elapsed:.3f}"
            )
            notes.append(
                f"pressure={pressure:.1f}MPa outcome=success liquidus_temp_c={trace.liquidus_temp_c} elapsed_s={elapsed:.3f}"
            )
        except Exception as exc:
            elapsed = time.time() - t_start
            tb = traceback.format_exc()
            trace = ProbeTrace(
                pressure_mpa=float(pressure),
                temperature_range_c="1200->700",
                mode="liam_find_wet_liquidus_direct",
                outcome="exception",
                liquidus_temp_c=None,
                elapsed_s=float(elapsed),
                exception_type=type(exc).__name__,
                exception_message=str(exc),
                traceback_text=tb,
            )
            print(
                f"phase1 pressure={pressure:.1f}MPa outcome=exception type={type(exc).__name__} msg={exc} elapsed_s={elapsed:.3f}"
            )
            notes.append(
                f"pressure={pressure:.1f}MPa outcome=exception type={type(exc).__name__} msg={exc} elapsed_s={elapsed:.3f}"
            )
        traces.append(trace)

    _write_trace_csv(trace_csv_path, traces)
    trace_json_path.write_text(json.dumps([_json_safe(asdict(t)) for t in traces], indent=2), encoding="utf-8")

    # Phase 3: State-reuse A/B on the same direct liquidus-search probes.
    state_reuse_modes = [
        "reuse_state",
        "fresh_state_each_iteration",
        "fresh_state_after_exception",
        "fresh_state_probe_only",
    ]
    notes.append("phase=3 state_reuse_ab")
    notes.append(f"state_reuse_modes={state_reuse_modes}")
    phase3_call_traces: list[LiquidusCallTrace] = []
    phase3_mode_summaries: list[LiquidusModeSummary] = []

    for mode_name in state_reuse_modes:
        for pressure in pressures:
            t_start = time.time()
            try:
                equil, blk_cmp = _build_blk_cmp_from_liam_composition(
                    composition_dict,
                    t1_c=1200.0,
                    p_mpa=pressure,
                    fo2_offset=fo2_offset,
                )
                liquidus_temp, n_iter, ex_type, ex_msg, tb_text = _find_wet_liquidus_state_reuse_diagnostic(
                    helper,
                    equil=equil,
                    T1=1200.0,
                    T2=700.0,
                    pressure_mpa=float(pressure),
                    composition=blk_cmp,
                    fO2_offset=float(fo2_offset),
                    mode_name=mode_name,
                    call_traces=phase3_call_traces,
                )
                elapsed = time.time() - t_start
                outcome = "success" if ex_type is None else "exception"
                phase3_mode_summaries.append(
                    LiquidusModeSummary(
                        pressure_mpa=float(pressure),
                        mode=mode_name,
                        exec_mode="thread_timeout_wrapper",
                        outcome=outcome,
                        liquidus_temp_c=float(liquidus_temp) if liquidus_temp is not None else None,
                        elapsed_s=float(elapsed),
                        iterations_completed=int(n_iter),
                        exception_type=ex_type,
                        exception_message=ex_msg,
                        traceback_text=tb_text,
                    )
                )
                print(
                    f"phase3 mode={mode_name} pressure={pressure:.1f}MPa outcome={outcome} "
                    f"liquidus_temp_c={liquidus_temp} iterations={n_iter} elapsed_s={elapsed:.3f} "
                    f"exception={ex_type or '-'}"
                )
                notes.append(
                    f"phase3 mode={mode_name} pressure={pressure:.1f}MPa outcome={outcome} "
                    f"liquidus_temp_c={liquidus_temp} iterations={n_iter} elapsed_s={elapsed:.3f} "
                    f"exception={ex_type or '-'}"
                )
            except Exception as exc:
                elapsed = time.time() - t_start
                tb_text = traceback.format_exc()
                phase3_mode_summaries.append(
                    LiquidusModeSummary(
                        pressure_mpa=float(pressure),
                        mode=mode_name,
                        exec_mode="thread_timeout_wrapper",
                        outcome="exception",
                        liquidus_temp_c=None,
                        elapsed_s=float(elapsed),
                        iterations_completed=0,
                        exception_type=type(exc).__name__,
                        exception_message=str(exc),
                        traceback_text=tb_text,
                    )
                )
                print(
                    f"phase3 mode={mode_name} pressure={pressure:.1f}MPa outcome=exception "
                    f"type={type(exc).__name__} msg={exc} elapsed_s={elapsed:.3f}"
                )
                notes.append(
                    f"phase3 mode={mode_name} pressure={pressure:.1f}MPa outcome=exception "
                    f"type={type(exc).__name__} msg={exc} elapsed_s={elapsed:.3f}"
                )

    _write_liquidus_call_trace_csv(phase3_dir / "call_traces.csv", phase3_call_traces)
    (phase3_dir / "call_traces.json").write_text(
        json.dumps([_json_safe(asdict(t)) for t in phase3_call_traces], indent=2),
        encoding="utf-8",
    )
    with (phase3_dir / "mode_summaries.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "pressure_mpa",
                "mode",
                "exec_mode",
                "outcome",
                "liquidus_temp_c",
                "elapsed_s",
                "iterations_completed",
                "exception_type",
                "exception_message",
                "traceback_text",
            ],
        )
        writer.writeheader()
        for row in [_json_safe(asdict(t)) for t in phase3_mode_summaries]:
            writer.writerow(row)
    (phase3_dir / "mode_summaries.json").write_text(
        json.dumps([_json_safe(asdict(t)) for t in phase3_mode_summaries], indent=2),
        encoding="utf-8",
                )

    # Phase 4: Timeout-wrapper A/B (same liquidus-search harness, fixed state modes)
    timeout_exec_modes = [
        ("raw_execute_no_timeout_wrapper", "raw_execute", False),
        ("thread_timeout_wrapper_current", "thread_timeout_wrapper", False),
        ("thread_timeout_wrapper_with_equil_reset_after_none", "thread_timeout_wrapper", True),
    ]
    timeout_state_modes = ["reuse_state", "fresh_state_probe_only"]
    notes.append("phase=4 timeout_wrapper_ab")
    notes.append(f"timeout_exec_modes={[m[0] for m in timeout_exec_modes]}")
    notes.append(f"timeout_state_modes={timeout_state_modes}")
    phase4_call_traces: list[LiquidusCallTrace] = []
    phase4_mode_summaries: list[LiquidusModeSummary] = []

    for state_mode in timeout_state_modes:
        for exec_mode_label, exec_mode_impl, reset_equil_on_none in timeout_exec_modes:
            for pressure in pressures:
                t_start = time.time()
                try:
                    def _rebuild():
                        return _build_blk_cmp_from_liam_composition(
                            composition_dict,
                            t1_c=1200.0,
                            p_mpa=pressure,
                            fo2_offset=fo2_offset,
                        )

                    equil, blk_cmp = _rebuild()
                    trace_mode_label = f"{state_mode}|{exec_mode_label}"
                    liquidus_temp, n_iter, ex_type, ex_msg, tb_text = _find_wet_liquidus_state_reuse_diagnostic(
                        helper,
                        equil=equil,
                        T1=1200.0,
                        T2=700.0,
                        pressure_mpa=float(pressure),
                        composition=blk_cmp,
                        fO2_offset=float(fo2_offset),
                        mode_name=state_mode,
                        call_traces=phase4_call_traces,
                        exec_mode=exec_mode_impl,
                        trace_mode_label=trace_mode_label,
                        rebuild_equil_comp_fn=_rebuild,
                        reset_equil_on_none=reset_equil_on_none,
                    )
                    elapsed = time.time() - t_start
                    outcome = "success" if ex_type is None else "exception"
                    phase4_mode_summaries.append(
                        LiquidusModeSummary(
                            pressure_mpa=float(pressure),
                            mode=state_mode,
                            exec_mode=exec_mode_label,
                            outcome=outcome,
                            liquidus_temp_c=float(liquidus_temp) if liquidus_temp is not None else None,
                            elapsed_s=float(elapsed),
                            iterations_completed=int(n_iter),
                            exception_type=ex_type,
                            exception_message=ex_msg,
                            traceback_text=tb_text,
                        )
                    )
                    print(
                        f"phase4 state_mode={state_mode} exec_mode={exec_mode_label} pressure={pressure:.1f}MPa "
                        f"outcome={outcome} liquidus_temp_c={liquidus_temp} iterations={n_iter} "
                        f"elapsed_s={elapsed:.3f} exception={ex_type or '-'}"
                    )
                    notes.append(
                        f"phase4 state_mode={state_mode} exec_mode={exec_mode_label} pressure={pressure:.1f}MPa "
                        f"outcome={outcome} liquidus_temp_c={liquidus_temp} iterations={n_iter} "
                        f"elapsed_s={elapsed:.3f} exception={ex_type or '-'}"
                    )
                except Exception as exc:
                    elapsed = time.time() - t_start
                    tb_text = traceback.format_exc()
                    phase4_mode_summaries.append(
                        LiquidusModeSummary(
                            pressure_mpa=float(pressure),
                            mode=state_mode,
                            exec_mode=exec_mode_label,
                            outcome="exception",
                            liquidus_temp_c=None,
                            elapsed_s=float(elapsed),
                            iterations_completed=0,
                            exception_type=type(exc).__name__,
                            exception_message=str(exc),
                            traceback_text=tb_text,
                        )
                    )
                    print(
                        f"phase4 state_mode={state_mode} exec_mode={exec_mode_label} pressure={pressure:.1f}MPa "
                        f"outcome=exception type={type(exc).__name__} msg={exc} elapsed_s={elapsed:.3f}"
                    )
                    notes.append(
                        f"phase4 state_mode={state_mode} exec_mode={exec_mode_label} pressure={pressure:.1f}MPa "
                        f"outcome=exception type={type(exc).__name__} msg={exc} elapsed_s={elapsed:.3f}"
                    )

    _write_liquidus_call_trace_csv(phase4_dir / "call_traces.csv", phase4_call_traces)
    (phase4_dir / "call_traces.json").write_text(
        json.dumps([_json_safe(asdict(t)) for t in phase4_call_traces], indent=2),
        encoding="utf-8",
    )
    with (phase4_dir / "mode_summaries.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "pressure_mpa",
                "mode",
                "exec_mode",
                "outcome",
                "liquidus_temp_c",
                "elapsed_s",
                "iterations_completed",
                "exception_type",
                "exception_message",
                "traceback_text",
            ],
        )
        writer.writeheader()
        for row in [_json_safe(asdict(t)) for t in phase4_mode_summaries]:
            writer.writerow(row)
    (phase4_dir / "mode_summaries.json").write_text(
        json.dumps([_json_safe(asdict(t)) for t in phase4_mode_summaries], indent=2),
        encoding="utf-8",
    )

    summary = {
        "phase": 1,
        "mode": "minimal_liquidus_reproducer",
        "liam_workbook": str(LIAM_WORKBOOK),
        "liam_helper": str(LIAM_HELPER),
        "probe_pressures_mpa": pressures,
        "num_success": sum(1 for t in traces if t.outcome == "success"),
        "num_exception": sum(1 for t in traces if t.outcome == "exception"),
        "trace_csv_path": str(trace_csv_path),
        "trace_json_path": str(trace_json_path),
        "notes_path": str(workflow_notes_path),
        "phase3_state_reuse_ab": {
            "modes": state_reuse_modes,
            "call_traces_csv_path": str(phase3_dir / "call_traces.csv"),
            "mode_summaries_csv_path": str(phase3_dir / "mode_summaries.csv"),
            "num_mode_runs": len(phase3_mode_summaries),
            "num_mode_success": sum(1 for t in phase3_mode_summaries if t.outcome == "success"),
            "num_mode_exception": sum(1 for t in phase3_mode_summaries if t.outcome == "exception"),
        },
        "phase4_timeout_ab": {
            "state_modes": timeout_state_modes,
            "exec_modes": [m[0] for m in timeout_exec_modes],
            "call_traces_csv_path": str(phase4_dir / "call_traces.csv"),
            "mode_summaries_csv_path": str(phase4_dir / "mode_summaries.csv"),
            "num_mode_runs": len(phase4_mode_summaries),
            "num_mode_success": sum(1 for t in phase4_mode_summaries if t.outcome == "success"),
            "num_mode_exception": sum(1 for t in phase4_mode_summaries if t.outcome == "exception"),
        },
    }
    summary_json_path.write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    workflow_notes_path.write_text("\n".join(notes) + "\n", encoding="utf-8")
    print(f"phase1 artifacts_root={run_root}")
    print(f"phase1 summary={summary_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
