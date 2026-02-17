"""
Package wrapper: csv_to_samples.py

This module exposes `csv_to_samples_list` for import into notebooks.
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

    See original module for full documentation and heuristics.
    """
    # Resolve filepath carefully
    if filepath:
        if os.path.isabs(filename):
            path = filename
        else:
            path = os.path.join(filepath, filename)
    else:
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

    # Load raw first to make robust orientation decisions
    raw = pd.read_csv(path)

    # Define oxide-like heuristic for labels
    def oxide_like_label(s: str) -> bool:
        s = str(s).strip()
        if s == '':
            return False
        s_up = s.upper()
        # Accept explicit common tokens
        if s_up in ("LOI", "SUM"):
            return True
        # Require that oxide-like labels contain at least one letter and an 'O' (e.g., SiO2, TiO2, FeO)
        has_letter = bool(re.search('[A-Za-z]', s_up))
        has_o = 'O' in s_up
        if has_letter and has_o:
            return True
        # Also accept common short forms like 'FeO*' (contains O) or patterns like 'P2O5'
        if re.search(r'O\d', s_up):
            return True
        return False

    # Check first column values (likely oxide names if the file is in expected orientation)
    first_col_vals = raw.iloc[:, 0].astype(str)
    first_col_oxide_frac = float(np.mean([oxide_like_label(x) for x in first_col_vals])) if len(first_col_vals) > 0 else 0.0

    # Check header columns: sometimes the first header is an empty index column (Unnamed: 0)
    cols = list(raw.columns)
    cols_excl0 = cols[1:] if len(cols) > 1 else cols
    header_cols_oxide_frac_excl0 = float(np.mean([oxide_like_label(x) for x in cols_excl0])) if len(cols_excl0) > 0 else 0.0

    if first_col_oxide_frac >= 0.5:
        df = raw.set_index(raw.columns[0])
    elif header_cols_oxide_frac_excl0 >= 0.5:
        df = raw.set_index(raw.columns[0]).T
    else:
        try:
            df_try = pd.read_csv(path, index_col=index_col)
        except Exception:
            df_try = raw.set_index(raw.columns[0])

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
