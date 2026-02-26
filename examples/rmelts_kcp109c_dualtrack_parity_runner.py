from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

import sys

sys.path.insert(0, "/Users/lopezama/PycharmProjects/sci-cluster")
import rmelts_mc_pipeline as rmp
from examples.rmelts_kcp109c_liquidus_matmul_rootcause import _read_liam_init_cond


LIAM_WORKBOOK = Path("/Users/lopezama/Downloads/Parallel_MELTS_KCP-109C_dP25.0_02-25_8cores.xlsx")
HELPER_DIR = Path("/Users/lopezama/Downloads")
OUT_ROOT = Path("/Users/lopezama/PycharmProjects/sci-cluster/review_outputs/rmelts_parity_dualtrack_compare")


def _now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _note(log_path: Path, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


@contextlib.contextmanager
def _temp_env(name: str, value: str | None):
    old = os.environ.get(name)
    try:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = str(value)
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


def _build_liam_exact_rowwise_csv(out_dir: Path) -> Path:
    comp, _params = _read_liam_init_cond(LIAM_WORKBOOK)
    row: dict[str, Any] = {"SampleID": "KCP109C"}
    for k, v in comp.items():
        row[str(k)] = v
    df = pd.DataFrame([row])
    out_path = out_dir / "kcp109c_liam_exact_rowwise.csv"
    df.to_csv(out_path, index=False)
    return out_path


def _parse_pressure_from_workbook(workbook_path: Path) -> dict[str, Any]:
    wb = openpyxl.load_workbook(workbook_path, data_only=True)
    try:
        helper_sheet = rmp._read_pressure_analysis_sheet_from_workbook(wb)
        fallback = rmp._compute_pressure_analysis_from_workbook(  # type: ignore[attr-defined]
            wb,
            prefer_existing_sheet=False,
            residual_threshold=10.0,
        )
        out = {
            "helper_sheet": helper_sheet,
            "fallback": fallback,
        }
        return out
    finally:
        wb.close()


def _run_track(root: Path, log_path: Path, track_name: str, helper_impl: str) -> dict[str, Any]:
    track_root = root / f"track_{track_name}"
    prep_root = track_root / "prep"
    run_root = track_root / "runs"
    prep_root.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)

    rowwise_csv = _build_liam_exact_rowwise_csv(prep_root)
    conv = rmp.MC_to_csv_rMELTS(
        rowwise_csv,
        output_dir=prep_root,
        dataset_name="kcp109c_liam_exact_prep",
        sample_id_col="SampleID",
        fe_total_col="FeOt",
        h2o_col="H2O",
        validate_only=True,
    )

    _note(log_path, f"Running track={track_name} helper_impl={helper_impl}")
    with _temp_env("RMELTS_INTERNAL_HELPER_IMPLEMENTATION", helper_impl):
        run = rmp.rMELTS_run(
            conv.prepared_mc_csv_path,
            output_dir=run_root,
            dataset_name=f"kcp109c_dualtrack_{track_name}",
            T1=1100,
            T2=700,
            dT=1,
            P1=400,
            P2=50,
            dP=25,
            fO2_constraint="TRUE",
            fO2_buffer="NNO",
            fO2_offset=0.0,
            helper_dir=str(HELPER_DIR),
            backend="import",
            max_composition_workers=1,
            max_pressure_workers=11,
            pressure_residual_threshold_C=10.0,
            verbose=False,
        )

    manifest_df = pd.read_csv(run.manifest_csv_path)
    manifest_row = manifest_df.iloc[0].to_dict() if not manifest_df.empty else {}
    excel_path = Path(str(manifest_row.get("excel_path", ""))) if manifest_row.get("excel_path") else None
    wb_pressures = None
    if excel_path and excel_path.exists():
        wb_pressures = _parse_pressure_from_workbook(excel_path)
    basis = rmp.rMELTS_geobarometry_basis(
        run.manifest_csv_path,
        phases=["quartz", "feldspar", "feldspar_1"],
        include_system=True,
        include_liquid=True,
        missing_phase_policy="skip_point",
    )
    phase_counts = {k: int(len(v)) for k, v in basis.phase_tables.items()}

    return {
        "track_name": track_name,
        "helper_implementation": helper_impl,
        "prepared_mc_csv_path": conv.prepared_mc_csv_path,
        "run_result": {
            "run_dir": run.run_dir,
            "manifest_csv_path": run.manifest_csv_path,
            "summary": run.summary,
        },
        "manifest_row": manifest_row,
        "phase_counts": phase_counts,
        "workbook_pressure_analysis": wb_pressures,
    }


def main() -> None:
    tag = _now_tag()
    root = OUT_ROOT / time.strftime("%Y-%m-%d") / f"kcp109c_dualtrack_parity_{tag}"
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "workflow_notes.log"
    summary_json = root / "dualtrack_parity_summary.json"
    summary_csv = root / "dualtrack_parity_summary.csv"

    tracks_env = os.environ.get("RMELTS_DUALTRACK_TRACKS", "patched_profile_k,liam_clone").strip()
    requested_tracks = [t.strip() for t in tracks_env.split(",") if t.strip()]
    track_map = {
        "patched_profile_k": ("patched_profile_k", "patched_profile_k"),
        "liam_clone": ("liam_clone", "liam_clone"),
    }
    tracks = [track_map[t] for t in requested_tracks if t in track_map]
    if not tracks:
        raise ValueError("No valid tracks requested. Use RMELTS_DUALTRACK_TRACKS with patched_profile_k and/or liam_clone.")

    _note(log_path, f"Starting dual-track parity runner. tracks={requested_tracks}")

    liam_baseline = _parse_pressure_from_workbook(LIAM_WORKBOOK)
    results: list[dict[str, Any]] = []
    for track_name, helper_impl in tracks:
        results.append(_run_track(root, log_path, track_name, helper_impl))

    rows = []
    liam_p2 = rmp._safe_float(liam_baseline.get("helper_sheet", {}).get("P_2phase_qtz_fsp_MPa"))  # type: ignore[attr-defined]
    for rec in results:
        manifest_row = rec.get("manifest_row", {})
        p2 = rmp._safe_float(manifest_row.get("P_2phase_qtz_fsp_MPa"))  # type: ignore[attr-defined]
        rows.append(
            {
                "track_name": rec["track_name"],
                "helper_implementation": rec["helper_implementation"],
                "status": manifest_row.get("status"),
                "num_pressure_steps": manifest_row.get("num_pressure_steps"),
                "num_data_points": manifest_row.get("num_data_points"),
                "P_2phase_qtz_fsp_MPa": p2,
                "P_3phase_qtz_fsp_fsp1_MPa": manifest_row.get("P_3phase_qtz_fsp_fsp1_MPa"),
                "delta_P2_vs_liam_MPa": (None if liam_p2 is None or p2 is None else float(p2 - liam_p2)),
                "manifest_csv_path": rec["run_result"]["manifest_csv_path"],
                "run_dir": rec["run_result"]["run_dir"],
            }
        )

    pd.DataFrame(rows).to_csv(summary_csv, index=False)
    payload = {
        "liam_baseline_workbook": str(LIAM_WORKBOOK),
        "liam_baseline_pressure_analysis": liam_baseline,
        "tracks_requested": requested_tracks,
        "results": results,
        "summary_rows": rows,
        "summary_csv": str(summary_csv),
    }
    summary_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    _note(log_path, f"Wrote dual-track parity summary JSON: {summary_json}")
    _note(log_path, f"Wrote dual-track parity summary CSV: {summary_csv}")


if __name__ == "__main__":
    main()
