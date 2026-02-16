"""
csv_to_samples.py

Standalone helper to read CSVs where oxide names are arranged vertically (index) and samples are horizontal
(columns). Produces a list-of-lists where each sub-list represents a sample (sample name first by default)
followed by the values for each oxide in the order returned.

This version improves filepath resolution and makes the transpose-detection heuristic more robust by
checking both numeric content and presence of alphabetic characters in index/column labels.

Behavior:
- Auto-detects and transposes if the CSV appears to be transposed.
- Coerces non-numeric values to NaN so plotting libraries will skip them (left blank).
- Returns (samples_list, oxides_order, df_numeric)

Includes a small self-test when run as a script that writes two temporary CSVs and prints parsed output.
"""
from typing import List, Tuple, Optional
import os
import re
import pandas as pd
import numpy as np


def csv_to_samples_list(
    filename: str,
    filepath: Optional[str] = None,
    index_col: int = 0,
    include_name: bool = True,
    transpose_if_needed: bool = True,
) -> Tuple[List[List], List[str], pd.DataFrame]:
    """Read a CSV and return samples as a list-of-lists.

    Parameters:
        filename: CSV filename or path. If filepath is provided it is joined with filename.
        filepath: Optional folder path. If None the function will try some sensible defaults:
            - If filename is absolute, use it.
            - Else look for filename under the module directory's `sci-data/` folder.
            - Else treat filename as relative to the module directory.
        index_col: Column to use as the row index (default 0, expecting oxide names there).
        include_name: If True, each returned sub-list starts with the sample name.
        transpose_if_needed: Attempt to auto-detect and transpose input when orientation seems inverted.

    Returns:
        samples_list: list of lists. Each sub-list is [sample_name, v1, v2, ...] if include_name else [v1, v2, ...].
        oxides_order: list of oxide names (order corresponds to values for each sample).
        df_numeric: pandas DataFrame with oxides as index and samples as columns (numeric values, NaN for invalid).
    """
    # Resolve filepath carefully
    if filepath:
        # If filename is absolute, respect it; else join
        if os.path.isabs(filename):
            path = filename
        else:
            path = os.path.join(filepath, filename)
    else:
        # No filepath provided
        if os.path.isabs(filename):
            path = filename
        else:
            base_dir = os.path.dirname(__file__)
            alt = os.path.join(base_dir, 'sci-data', filename)
            if os.path.exists(alt):
                path = alt
            else:
                path = os.path.join(base_dir, filename)

    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV file not found: {path}")

    # Read CSV (try with index_col, fallback to setting first column as index)
    # Load raw first to make robust orientation decisions
    raw = pd.read_csv(path)

    # Define oxide-like heuristic for labels
    def oxide_like_label(s: str) -> bool:
        s = str(s)
        if re.search('[A-Za-z]', s) is None:
            return False
        # oxide names often contain an 'O' and optionally digits (e.g., SiO2)
        return bool(re.search('O', s, re.IGNORECASE) or re.search('\d', s))

    # Check first column values (likely oxide names if the file is in expected orientation)
    first_col_vals = raw.iloc[:, 0].astype(str)
    first_col_oxide_frac = float(np.mean([oxide_like_label(x) for x in first_col_vals])) if len(first_col_vals) > 0 else 0.0

    # Check header columns: sometimes the first header is an empty index column (Unnamed: 0)
    cols = list(raw.columns)
    cols_excl0 = cols[1:] if len(cols) > 1 else cols
    header_cols_oxide_frac_excl0 = float(np.mean([oxide_like_label(x) for x in cols_excl0])) if len(cols_excl0) > 0 else 0.0

    # Decide orientation deterministically:
    # - If the first column values look like oxide names -> treat first column as index (oxides)
    # - Else if the header columns (excluding first) look like oxide names -> the file was transposed (samples are rows)
    # - Else fall back to numeric-fraction heuristic comparing current vs transposed numeric cell counts
    if first_col_oxide_frac >= 0.5:
        df = raw.set_index(raw.columns[0])
    elif header_cols_oxide_frac_excl0 >= 0.5:
        df = raw.set_index(raw.columns[0]).T
    else:
        # Fallback: try reading with index_col, then choose orientation with more numeric cells
        try:
            df_try = pd.read_csv(path, index_col=index_col)
        except Exception:
            df_try = raw.set_index(raw.columns[0])
        # choose orientation with higher numeric fraction
        def numeric_cell_fraction(dframe: pd.DataFrame) -> float:
            flat = pd.Series(dframe.values.ravel())
            coerced = pd.to_numeric(flat, errors='coerce')
            if len(coerced) == 0:
                return 0.0
            return coerced.notna().mean()

        if numeric_cell_fraction(df_try.T) > numeric_cell_fraction(df_try) + 0.01:
            df = df_try.T
        else:
            df = df_try

    # Helper: fraction of numeric values in a Series
    def numeric_fraction_series(s: pd.Series) -> float:
        coerced = pd.to_numeric(s, errors='coerce')
        if len(coerced) == 0:
            return 0.0
        return coerced.notna().mean()

    # Compute numeric fraction across columns
    col_fracs = [numeric_fraction_series(df[col]) for col in df.columns] if len(df.columns) > 0 else [0.0]
    avg_col_frac = float(np.mean(col_fracs))

    # Fraction numeric in index (not usually numeric for oxide names)
    try:
        idx_series = pd.Series(list(df.index))
        idx_numeric_frac = numeric_fraction_series(idx_series)
    except Exception:
        idx_numeric_frac = 0.0

    # Fraction of index entries that contain alphabetic characters (likely oxide names)
    try:
        idx_alpha_frac = float(np.mean([bool(re.search('[A-Za-z]', str(x))) for x in df.index]))
    except Exception:
        idx_alpha_frac = 0.0

    # Fraction of column names that contain alphabetic characters (likely sample names)
    try:
        colname_alpha_frac = float(np.mean([bool(re.search('[A-Za-z]', str(x))) for x in df.columns]))
    except Exception:
        colname_alpha_frac = 0.0

    # Heuristic: compute how "oxide-like" a label is (letters + digit OR contains 'O')
    def oxide_like(s: str) -> bool:
        s = str(s)
        has_letter = bool(re.search('[A-Za-z]', s))
        has_digit = bool(re.search('\\d', s))
        contains_o = bool(re.search('O', s, re.IGNORECASE))
        return has_letter and (has_digit or contains_o)

    try:
        idx_oxide_frac = float(np.mean([oxide_like(x) for x in df.index]))
    except Exception:
        idx_oxide_frac = 0.0
    try:
        col_oxide_frac = float(np.mean([oxide_like(x) for x in df.columns]))
    except Exception:
        col_oxide_frac = 0.0

    # Coerce all values to numeric (non-numeric -> NaN)
    df_numeric = df.apply(lambda c: pd.to_numeric(c, errors='coerce'))

    # Ensure index and column labels are strings for consistency
    df_numeric.index = df_numeric.index.map(lambda x: str(x))
    df_numeric.columns = df_numeric.columns.map(lambda x: str(x))

    oxides_order = list(df_numeric.index)

    samples_list = []
    for sample_name in df_numeric.columns:
        values = list(df_numeric[sample_name].values)
        if include_name:
            samples_list.append([sample_name] + values)
        else:
            samples_list.append(values)

    return samples_list, oxides_order, df_numeric


# Self test when run directly
if __name__ == '__main__':
    base = os.path.dirname(__file__)
    ok_path = os.path.join(base, 'test_ok.csv')
    trans_path = os.path.join(base, 'test_transposed.csv')

    oxides = ['SiO2', 'Al2O3', 'FeO', 'MgO']
    data = {
        'Sample_A': [65.1, 15.2, 2.1, 3.5],
        'Sample_B': [64.8, 14.9, 'NA', 3.2],
        'Sample_C': [np.nan, 15.0, 2.0, 3.1],
    }
    df_ok = pd.DataFrame(data, index=oxides)
    df_ok.to_csv(ok_path)

    # Transposed version (samples as rows)
    df_trans = df_ok.T
    df_trans.to_csv(trans_path)

    print('=== Test: proper orientation ===')
    s_list, ox_order, df_num = csv_to_samples_list('test_ok.csv', filepath=base)
    print('oxides_order:', ox_order)
    print('samples_list:')
    for s in s_list:
        print(s)

    print('\n=== Test: transposed input (auto-detect) ===')
    s_list_t, ox_order_t, df_num_t = csv_to_samples_list('test_transposed.csv', filepath=base)
    print('oxides_order:', ox_order_t)
    print('samples_list:')
    for s in s_list_t:
        print(s)
