# Package-level function summary for sci_helpers
# Exported functions (public API):
# - csv_to_samples_list(filepath) -> (samples_list, oxides_order, df_num)
# - wrangle_dataframe(df, oxide_names=None, anchor='SiO2', min_oxides=3, blank_row_tolerance=50, auto_name_prefix='sample_auto_', write_out=None) -> List[Dict]
# - wrangle_excel(path, sheet_name=None, **kwargs) -> List[Dict]
# - compare_wrangled(file_a, file_b, path) -> None
#     """Simple print-only comparator for notebook use. Prints confirmations only, no return value."""
# - compare_wrangled_detailed(file_a, file_b, data_path, tol=1e-12, list_all=False, output_csv=None) -> (bool, mismatches)
#     """Programmatic comparator returning (ok, mismatches). Use this when you need mismatch details."""
#
# Notes and recommendations:
# - The comparator supports searching for files anywhere under a provided `data_path` (recursive lookup).
#   This means you may pass filenames, relative paths (e.g., 'Aplites_XRF_data/wrangled_dataset_1.csv')
#   or absolute paths. When supplying Windows paths with backslashes in Python source, prefer raw strings
#   (r"C:\path\to\sci-data") to avoid escape-sequence warnings.
# - Recommended import pattern to guarantee the callable is bound in long-lived kernels:
#     from sci_helpers.compare_wrangled import compare_wrangled
#   This imports the print-only comparator directly. For programmatic access (returning mismatches)
#   import `compare_wrangled_detailed` from the same module:
#     from sci_helpers.compare_wrangled import compare_wrangled_detailed
#
# - The package-level module attempts to import and bind key functions directly so `from sci_helpers import compare_wrangled`
#   will provide the callable in usual interactive use. If you encounter import issues in old kernels, restart the
#   kernel or import the function directly from the submodule as shown above.
#
# End of header notes.

# Defensive binding: import the comparator function(s) directly from the submodule
# This ensures `from sci_helpers import compare_wrangled` gives the function even in long-lived kernels
try:
    from .compare_wrangled import compare_wrangled as compare_wrangled, compare_wrangled_detailed as compare_wrangled_detailed
except Exception:
    # fallback: avoid hard failure during import; the names may be bound later
    compare_wrangled = None
    compare_wrangled_detailed = None

from .csv_to_samples import csv_to_samples_list
from .mc_wrangler import wrangle_dataframe, wrangle_excel

__all__ = ["csv_to_samples_list", "wrangle_dataframe", "wrangle_excel", "compare_wrangled", "compare_wrangled_detailed"]
