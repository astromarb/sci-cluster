import MeltsHelperFunctions as func
import MeltsFP as func_fp
import sys
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv_filename", type=str, default="MarvinApliteComps.csv",
                        help="CSV file containing composition data (default: MarvinApliteComps.csv)")
    parser.add_argument("--max_composition_workers", type=int, default=1,
                        help="Number of parallel composition workers (default: 1)")
    parser.add_argument("--max_pressure_workers", type=int, default=8,
                        help="Number of parallel pressure workers per composition (default: 8)")
    parser.add_argument("--verbose", action="store_true", default=True,
                        help="Enable verbose output (default: True)")
    parser.add_argument("--use_futureproof", action="store_true", default=False,
                        help="Use futureproof implementation instead of standard (default: False)")
    args = parser.parse_args()

    if args.use_futureproof:
        print("Using futureproof implementation...")
        results = func_fp.parallel_melts_main_loop(args.csv_filename, args.max_composition_workers, 
                args.max_pressure_workers, args.verbose)
    else:
        print("Using standard implementation...")
        results = func.parallel_melts_main_loop(args.csv_filename, args.max_composition_workers, 
                args.max_pressure_workers, args.verbose)
    