# Package-level function summary for sci_helpers
# Exported functions (public API):
# - csv_to_samples_list(filepath) -> (samples_list, oxides_order, df_num)
# - wrangle_dataframe(df, oxide_names=None, anchor='SiO2', min_oxides=3, blank_row_tolerance=50, auto_name_prefix='sample_auto_', write_out=None) -> List[Dict]
# - wrangle_excel(path, sheet_name=None, **kwargs) -> List[Dict]
# - compare_wrangled(file_a, file_b, path) -> None
#     """Simple print-only comparator for notebook use. Prints confirmations only, no return value."""
# - compare_wrangled_detailed(file_a, file_b, data_path, tol=1e-12, list_all=False, output_csv=None) -> (bool, mismatches)
#     """Programmatic comparator returning (ok, mismatches). Use this when you need mismatch details."""

from .csv_to_samples import csv_to_samples_list
from .mc_wrangler import wrangle_dataframe, wrangle_excel
from .compare_wrangled import compare_wrangled, compare_wrangled_detailed

__all__ = ["csv_to_samples_list", "wrangle_dataframe", "wrangle_excel", "compare_wrangled", "compare_wrangled_detailed"]
