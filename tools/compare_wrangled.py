"""CLI wrapper for sci_helpers.compare_wrangled_detailed.

Usage:
  python tools/compare_wrangled.py --file-a A --file-b B --data-path path [--tol 1e-8] [--list-all] [--output-csv out.csv]
"""

import argparse
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from sci_helpers.compare_wrangled import compare_wrangled_detailed

parser = argparse.ArgumentParser(description="Compare two wrangled datasets")
parser.add_argument("--file-a", required=True, help="Filename of first wrangled dataset (with or without extension)")
parser.add_argument("--file-b", required=True, help="Filename of second wrangled dataset (with or without extension)")
parser.add_argument("--data-path", required=True, help="Path to directory containing the wrangled dataset files")
parser.add_argument("--tol", type=float, default=1e-12, help="Absolute tolerance for numeric comparisons")
parser.add_argument("--list-all", action="store_true", help="Collect and list all mismatches")
parser.add_argument("--output-csv", default=None, help="Optional CSV to write mismatch rows")
args = parser.parse_args()

ok, mismatches = compare_wrangled_detailed(
    args.file_a,
    args.file_b,
    args.data_path,
    tol=args.tol,
    list_all=args.list_all,
    output_csv=args.output_csv,
)
if ok:
    print("\nExit: datasets are equal")
    sys.exit(0)
print("\nExit: datasets are NOT equal")
sys.exit(2)
