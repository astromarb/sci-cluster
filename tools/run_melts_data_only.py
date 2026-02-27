#!/usr/bin/env python3
"""Run one-sample MELTS data-only pipeline (no pressure analysis by default)."""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib
import json
import os
from pathlib import Path
import sys

import pandas as pd

# Keep matplotlib cache writable when transitive imports pull plotting modules.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

# Allow direct execution: `python tools/run_melts_data_only.py ...`
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sci_helpers.liam_input_converter import (
    DEFAULT_REQUIRED_DATASET_OXIDES,
    DEFAULT_REQUIRED_OUTPUT_OXIDES,
    write_liam_input_csv,
)
from sci_helpers.melts_data_extractor import (
    extract_calls_long,
    extract_calls_wide,
    write_workbook_tables,
)

DEFAULT_MELTS_PARAMS = {
    "Model": "rhyolite-MELTS_v1.0.x",
    "Calculation": "QF_P_Calc",
    "T1": 1100,
    "T2": 700,
    "ΔT": 1,
    "T unit": "C",
    "P1": 400,
    "P2": 50,
    "ΔP": 25,
    "P unit": "MPa",
    "fO2 offset": 0,
    "fO2 buffer": "NNO",
    "fO2 constraint": True,
    "ΔH": 0.5,
    "ΔV": 0,
    "ΔS": 0,
}

DEFAULT_FIXED_OXIDE_OVERRIDES = {
    "Fe2O3": 0.0,
    "Cr2O3": 0.0,
    "NiO": 0.0,
    "CoO": 0.0,
    "H2O": 13.0,
    "CO2": 0.0,
    "SO3": 0.0,
    "Cl2O-1": 0.0,
    "F2O -1": 0.0,
}


def _parse_json_arg(inline_json: str | None, json_path: str | None) -> dict:
    merged: dict = {}
    if json_path:
        path = Path(json_path).expanduser().resolve()
        with path.open("r", encoding="utf-8") as fh:
            merged.update(json.load(fh))
    if inline_json:
        merged.update(json.loads(inline_json))
    return merged


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_liam_module(liam_code_dir: Path, module_name: str):
    liam_code_dir = liam_code_dir.expanduser().resolve()
    if not liam_code_dir.exists():
        raise FileNotFoundError(f"Liam code directory not found: {liam_code_dir}")
    if str(liam_code_dir) not in sys.path:
        sys.path.insert(0, str(liam_code_dir))

    module = importlib.import_module(module_name)
    return importlib.reload(module)


def _resolve_result_workbook(run_dir: Path, result_filename: str | None) -> Path | None:
    if not result_filename:
        return None
    workbook = Path(result_filename)
    if not workbook.is_absolute():
        workbook = run_dir / workbook
    if not workbook.exists():
        return None
    return workbook.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wrangled-csv",
        default="sci-data/wrangled-outputs/wrangled_KCP-109-C_compositions.csv",
        help="Wrangled CSV path (first column must be sample IDs).",
    )
    parser.add_argument("--sample-id", default="KCP-109-B", help="Single sample ID to process.")
    parser.add_argument(
        "--liam-code-dir",
        default="vendor/LeiTesting",
        help="Directory containing MeltsHelperFunctions.py / MeltsFP.py.",
    )
    parser.add_argument(
        "--liam-module",
        choices=["MeltsHelperFunctions", "MeltsFP"],
        default="MeltsHelperFunctions",
        help="Vendor module to execute.",
    )
    parser.add_argument(
        "--output-root",
        default="outputs/melts-data-only-runs",
        help="Root output directory for run artifacts.",
    )
    parser.add_argument("--run-id", default=None, help="Optional fixed run id; default uses timestamp.")
    parser.add_argument(
        "--params-json",
        default=None,
        help="Optional JSON file overriding MELTS params.",
    )
    parser.add_argument(
        "--params-inline",
        default=None,
        help="Optional inline JSON object overriding MELTS params.",
    )
    parser.add_argument(
        "--fixed-overrides-inline",
        default=None,
        help="Optional inline JSON object for fixed oxide overrides.",
    )
    parser.add_argument(
        "--required-dataset-oxides",
        default=",".join(DEFAULT_REQUIRED_DATASET_OXIDES),
        help="Comma-separated required oxides from wrangled dataset.",
    )
    parser.add_argument(
        "--required-output-oxides",
        default=",".join(DEFAULT_REQUIRED_OUTPUT_OXIDES),
        help="Comma-separated output oxide order for Liam CSV.",
    )
    parser.add_argument("--max-composition-workers", type=int, default=1)
    parser.add_argument("--max-pressure-workers", type=int, default=1)
    parser.add_argument(
        "--run-pressure-analysis",
        action="store_true",
        help="Enable vendor pressure_calc stage (disabled by default for data-only prototype).",
    )
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable verbose vendor logging.",
    )
    args = parser.parse_args()

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.output_root).expanduser().resolve() / run_id
    liam_input_dir = run_dir / "liam-input"
    tables_dir = run_dir / "tables"
    run_dir.mkdir(parents=True, exist_ok=True)
    liam_input_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    user_params = _parse_json_arg(args.params_inline, args.params_json)
    user_fixed = _parse_json_arg(args.fixed_overrides_inline, None)

    melts_params = {**DEFAULT_MELTS_PARAMS, **user_params}
    fixed_oxide_overrides = {**DEFAULT_FIXED_OXIDE_OVERRIDES, **user_fixed}

    required_dataset_oxides = _parse_csv_list(args.required_dataset_oxides)
    required_output_oxides = _parse_csv_list(args.required_output_oxides)

    liam_input_csv = liam_input_dir / (
        f"liam_input_{Path(args.wrangled_csv).stem}__{args.sample_id}.csv"
    )
    liam_input_csv = write_liam_input_csv(
        wrangled_csv=args.wrangled_csv,
        sample_id=args.sample_id,
        output_csv=liam_input_csv,
        melts_params=melts_params,
        fixed_oxide_overrides=fixed_oxide_overrides,
        required_dataset_oxides=required_dataset_oxides,
        required_output_oxides=required_output_oxides,
    )

    liam_module = _load_liam_module(Path(args.liam_code_dir), args.liam_module)
    if not hasattr(liam_module, "parallel_melts_main_loop"):
        raise AttributeError(
            f"{args.liam_module} does not expose parallel_melts_main_loop. "
            "Check vendor code import path."
        )

    cwd_before = Path.cwd()
    os.chdir(run_dir)
    try:
        results = liam_module.parallel_melts_main_loop(
            compositions_csv=str(liam_input_csv),
            max_composition_workers=args.max_composition_workers,
            max_pressure_workers=args.max_pressure_workers,
            verbose=args.verbose,
            run_pressure_analysis=args.run_pressure_analysis,
        )
    finally:
        os.chdir(cwd_before)

    workbook_paths: list[Path] = []
    for result in results:
        workbook = _resolve_result_workbook(run_dir, result.get("filename"))
        if workbook is not None:
            workbook_paths.append(workbook)

    table_exports: list[Path] = []
    wide_frames: list[pd.DataFrame] = []
    long_frames: list[pd.DataFrame] = []
    for workbook_path in workbook_paths:
        table_exports.extend(write_workbook_tables(workbook_path, tables_dir))
        wide_frames.append(extract_calls_wide(workbook_path))
        long_frames.append(extract_calls_long(workbook_path))

    calls_wide = (
        pd.concat([df for df in wide_frames if not df.empty], ignore_index=True)
        if wide_frames
        else pd.DataFrame()
    )
    calls_long = (
        pd.concat([df for df in long_frames if not df.empty], ignore_index=True)
        if long_frames
        else pd.DataFrame()
    )
    calls_wide_path = run_dir / "calls_wide.csv"
    calls_long_path = run_dir / "calls_long.csv"
    calls_wide.to_csv(calls_wide_path, index=False)
    calls_long.to_csv(calls_long_path, index=False)

    summary = {
        "run_id": run_id,
        "wrangled_csv": str(Path(args.wrangled_csv).expanduser().resolve()),
        "sample_id": args.sample_id,
        "liam_module": args.liam_module,
        "liam_input_csv": str(liam_input_csv),
        "run_dir": str(run_dir),
        "workbooks": [str(path) for path in workbook_paths],
        "table_exports": [str(path) for path in table_exports],
        "calls_wide_csv": str(calls_wide_path),
        "calls_long_csv": str(calls_long_path),
        "run_pressure_analysis": bool(args.run_pressure_analysis),
        "results": results,
    }
    summary_path = run_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)

    print(f"Run complete: {run_id}")
    print(f"Liam input CSV: {liam_input_csv}")
    print(f"Workbook count: {len(workbook_paths)}")
    print(f"Tables exported: {len(table_exports)}")
    print(f"Calls wide CSV: {calls_wide_path}")
    print(f"Calls long CSV: {calls_long_path}")
    print(f"Summary JSON: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
