"""Run one wrangled sample through Liam's pressure implementation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from sci_helpers import (
    PreparedInputRunConfig,
    SingleLiamRunConfig,
    run_single_liam_pressure_from_prepared_csv,
    run_single_liam_pressure_from_wrangled,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one wrangled sample through Liam's pressure calculator."
    )
    parser.add_argument(
        "--prepared-liam-csv",
        default=None,
        help="Path to a preformatted Liam CSV (single-column input).",
    )
    parser.add_argument("--wrangled-csv", default=None, help="Path to wrangled CSV.")
    parser.add_argument("--sample-id", default=None, help="Sample ID in first wrangled column.")
    parser.add_argument("--vendor-code-dir", default="vendor/LeiTesting", help="Path to Liam vendor code.")
    parser.add_argument(
        "--thermoengine-src",
        default=None,
        help=(
            "Optional ThermoEngine source root used to bootstrap imports when the active "
            "environment does not resolve `from thermoengine import equilibrate` correctly."
        ),
    )
    parser.add_argument(
        "--run-root",
        default="outputs/liam-pressure-runs",
        help="Directory where run outputs will be written.",
    )
    parser.add_argument("--composition-workers", type=int, default=1, help="Liam composition workers.")
    parser.add_argument("--pressure-workers", type=int, default=1, help="Liam pressure workers.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose Liam logging.")

    parser.add_argument("--model", default="rhyolite-MELTS_v1.0.x", help="MELTS model row value.")
    parser.add_argument("--calculation", default="QF_P_Calc", help="Calculation row value.")
    parser.add_argument("--t1", type=float, default=1100.0, help="Starting temperature (C).")
    parser.add_argument("--t2", type=float, default=700.0, help="Ending temperature (C).")
    parser.add_argument("--dt", type=float, default=1.0, help="Temperature step (C).")
    parser.add_argument("--p1", type=float, default=400.0, help="Starting pressure (MPa).")
    parser.add_argument("--p2", type=float, default=50.0, help="Ending pressure (MPa).")
    parser.add_argument("--dp", type=float, default=25.0, help="Pressure step (MPa).")
    parser.add_argument("--fo2-offset", type=float, default=0.0, help="fO2 offset.")
    parser.add_argument("--fo2-buffer", default="NNO", help="fO2 buffer.")
    parser.add_argument(
        "--fo2-constraint",
        default="TRUE",
        choices=["TRUE", "FALSE", "true", "false"],
        help="fO2 constraint boolean.",
    )

    parser.add_argument("--h2o", type=float, default=None, help="Optional fixed H2O wt%% for Liam input.")
    parser.add_argument("--fe2o3", type=float, default=None, help="Optional fixed Fe2O3 wt%%.")
    parser.add_argument("--cr2o3", type=float, default=None, help="Optional fixed Cr2O3 wt%%.")
    parser.add_argument("--nio", type=float, default=None, help="Optional fixed NiO wt%%.")
    parser.add_argument("--coo", type=float, default=None, help="Optional fixed CoO wt%%.")
    parser.add_argument("--co2", type=float, default=None, help="Optional fixed CO2 wt%%.")
    parser.add_argument("--so3", type=float, default=None, help="Optional fixed SO3 wt%%.")
    parser.add_argument("--cl2o-1", type=float, default=None, help="Optional fixed Cl2O-1 wt%%.")
    parser.add_argument("--f2o-1", type=float, default=None, help="Optional fixed F2O-1 wt%%.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.thermoengine_src:
        os.environ["THERMOENGINE_SRC_ROOT"] = str(Path(args.thermoengine_src).expanduser().resolve())

    melts_params = {
        "Model": args.model,
        "Calculation": args.calculation,
        "T1": args.t1,
        "T2": args.t2,
        "ΔT": args.dt,
        "T unit": "C",
        "P1": args.p1,
        "P2": args.p2,
        "ΔP": args.dp,
        "P unit": "MPa",
        "fO2 offset": args.fo2_offset,
        "fO2 buffer": args.fo2_buffer,
        "fO2 constraint": args.fo2_constraint,
        "ΔH": 0.5,
        "ΔV": 0,
        "ΔS": 0,
    }

    fixed_oxide_overrides = {
        key: value
        for key, value in {
            "H2O": args.h2o,
            "Fe2O3": args.fe2o3,
            "Cr2O3": args.cr2o3,
            "NiO": args.nio,
            "CoO": args.coo,
            "CO2": args.co2,
            "SO3": args.so3,
            "Cl2O-1": args.cl2o_1,
            "F2O-1": args.f2o_1,
        }.items()
        if value is not None
    }

    if args.prepared_liam_csv:
        cfg = PreparedInputRunConfig(
            prepared_liam_csv=Path(args.prepared_liam_csv),
            max_composition_workers=args.composition_workers,
            max_pressure_workers=args.pressure_workers,
            vendor_code_dir=Path(args.vendor_code_dir),
            run_root_dir=Path(args.run_root),
            verbose=args.verbose,
        )
        summary = run_single_liam_pressure_from_prepared_csv(cfg)
    else:
        if not args.wrangled_csv or not args.sample_id:
            raise ValueError(
                "Provide either --prepared-liam-csv OR (--wrangled-csv and --sample-id)."
            )
        cfg = SingleLiamRunConfig(
            wrangled_csv=Path(args.wrangled_csv),
            sample_id=args.sample_id,
            melts_params=melts_params,
            fixed_oxide_overrides=fixed_oxide_overrides,
            max_composition_workers=args.composition_workers,
            max_pressure_workers=args.pressure_workers,
            vendor_code_dir=Path(args.vendor_code_dir),
            run_root_dir=Path(args.run_root),
            verbose=args.verbose,
        )
        summary = run_single_liam_pressure_from_wrangled(cfg)
    print(json.dumps(summary, indent=2))
    return 0 if summary["failed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
