"""Run ThermoEngine geobarometer + pressure-root + liquidus pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from sci_helpers import GeobarometerRunConfig, run_geobarometer_driver


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run row-wise sample compositions through ThermoEngine geobarometer, "
            "pressure root, and liquidus workflows."
        )
    )
    parser.add_argument("--input-csv", required=True, help="Row-wise input CSV path.")
    parser.add_argument(
        "--output-dir",
        default="outputs/thermoengine-geobarometer-runs",
        help="Directory where run artifacts are written.",
    )
    parser.add_argument(
        "--thermoengine-src",
        default=None,
        help=(
            "Optional ThermoEngine source root used to bootstrap imports when active "
            "site-packages resolve to an incompatible thermoengine variant."
        ),
    )
    parser.add_argument(
        "--pressure-bracket-bar",
        nargs=2,
        type=float,
        metavar=("P_LO", "P_HI"),
        default=None,
        help="Pressure bracket in bar for pressure root mode (required for pressure/all).",
    )
    parser.add_argument(
        "--pressure-grid-mpa",
        nargs=3,
        type=float,
        metavar=("P_START", "P_STOP", "P_STEP"),
        default=None,
        help="Override pressure grid for liquidus loop in MPa.",
    )
    parser.add_argument(
        "--mode",
        choices=["geobarometer", "pressure", "liquidus", "all"],
        default="all",
        help="Which solver stages to run.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose mode in config.")

    parser.set_defaults(benchmark_first=True)
    parser.add_argument(
        "--benchmark-first",
        dest="benchmark_first",
        action="store_true",
        help="Run canonical notebook benchmark before batch processing (default).",
    )
    parser.add_argument(
        "--no-benchmark-first",
        dest="benchmark_first",
        action="store_false",
        help="Disable benchmark gate.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.thermoengine_src:
        os.environ["THERMOENGINE_SRC_ROOT"] = str(Path(args.thermoengine_src).expanduser().resolve())

    config = GeobarometerRunConfig(
        input_csv=Path(args.input_csv),
        output_dir=Path(args.output_dir),
        mode=args.mode,
        pressure_bracket_bar=tuple(args.pressure_bracket_bar) if args.pressure_bracket_bar else None,
        pressure_grid_mpa=tuple(args.pressure_grid_mpa) if args.pressure_grid_mpa else None,
        benchmark_first=bool(args.benchmark_first),
        thermoengine_src_root=Path(args.thermoengine_src).expanduser().resolve() if args.thermoengine_src else None,
        verbose=bool(args.verbose),
    )
    summary = run_geobarometer_driver(config)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("failed_count", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
