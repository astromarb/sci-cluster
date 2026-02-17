"""
Compare two wrangled dataset files (CSV or Excel) for exact equality of headers and numeric values.

Function: compare_wrangled(file_a: str, file_b: str, data_path: str) -> bool
- file_a, file_b: filenames (with or without extension) inside data_path. If extension missing, .csv is assumed.
- data_path: folder containing the wrangled datasets (e.g., the `sci-data` folder or the `tools/wrangled_csvs` folder).

Behavior:
- Reads each file as a DataFrame with index_col=0.
- Canonicalizes oxide column labels using the same rules as `mc_wrangler`.
- Determines which dataset is smaller (rows*cols) and uses it as reference.
- Verifies that every sample (row) and oxide (column) in the small dataset exists in the large dataset.
- Compares numeric entries pairwise; treats NaN==NaN and uses exact equality (with a tiny tolerance) for floats.
- Prints human-readable checkpoints and returns True if datasets are equal, False otherwise.
"""
from typing import Tuple, List, Optional
import os
import pandas as pd
import numpy as np
from .mc_wrangler import canonicalize_oxide_label


def _read_wrangled(path: str) -> pd.DataFrame:
    # Supports CSV or Excel. Try CSV first, then Excel.
    if path.lower().endswith('.csv'):
        df = pd.read_csv(path, index_col=0)
    else:
        # try excel
        try:
            df = pd.read_excel(path, index_col=0)
        except Exception:
            # fallback to csv without extension
            df = pd.read_csv(path, index_col=0)
    # Ensure index and columns are strings
    df.index = df.index.map(lambda x: str(x).strip())
    df.columns = df.columns.map(lambda x: str(x).strip())
    return df


def _find_file(data_path: str, fname: str) -> str:
    """Locate a file by name under `data_path`.

    Behavior:
    - If `fname` is an absolute path and exists, return it (try common extensions if missing).
    - If `fname` is a path relative to `data_path` (may include subfolders), check the joined path.
    - Otherwise search recursively under `data_path` for a file whose relative path (from data_path)
      ends with the provided `fname` (useful for searching nested folders), or whose basename matches.
    - If no match, raise FileNotFoundError.
    """
    # Absolute path provided?
    if os.path.isabs(fname):
        if os.path.exists(fname):
            return fname
        # try with common extensions
        for ext in ('.csv', '.xlsx'):
            if os.path.exists(fname + ext):
                return fname + ext
        raise FileNotFoundError(f'Could not locate file {fname} (absolute path)')

    # Normalize separators so comparisons are consistent
    norm_fname = fname.replace('/', os.sep).replace('\\', os.sep).lstrip('.' + os.sep)

    # Direct join (covers the common case where fname includes a subfolder under data_path)
    candidate = os.path.join(data_path, norm_fname)
    if os.path.exists(candidate):
        return candidate
    # Try adding extensions if missing
    base, ext = os.path.splitext(candidate)
    if ext == '':
        for add_ext in ('.csv', '.xlsx'):
            if os.path.exists(candidate + add_ext):
                return candidate + add_ext

    # Recursive search: match either by basename or by relative-path "endswith" (robust for nested folders)
    # Prepare a normalized match string (use forward slashes for portability)
    match_norm = norm_fname.replace(os.sep, '/').lower()
    for root, dirs, files in os.walk(data_path):
        for f in files:
            # build relative path from data_path to this file (use forward slashes)
            rel = os.path.relpath(os.path.join(root, f), data_path).replace('\\', '/')
            rel_low = rel.lower()
            if rel_low.endswith(match_norm) or f.lower() == os.path.basename(match_norm).lower():
                return os.path.join(root, f)
            # also allow match when user provided name without extension
            if os.path.splitext(match_norm)[1] == '':
                if os.path.splitext(f)[0].lower() == os.path.basename(match_norm).lower():
                    return os.path.join(root, f)

    raise FileNotFoundError(f'Could not locate file {fname} in {data_path} (searched recursively)')


def compare_wrangled(file_a: str, file_b: str, data_path: Optional[str] = None, tol: float = 1e-12, list_all: bool = False, output_csv: Optional[str] = None, filepath: Optional[str] = None) -> Tuple[bool, List[Tuple[str,str,float,float,float]]]:
    """Compare two wrangled datasets located in `data_path` (or `filepath` alias).

    Returns (equal: bool, mismatches: list).
    Parameters:
      - data_path / filepath: path to directory containing the wrangled dataset files (either name accepted)
      - tol: absolute tolerance for numeric comparisons (default 1e-12)
      - list_all: if True, collect and print all mismatches; otherwise stop at first and report it
      - output_csv: optional path to write a CSV listing all mismatches
    """
    # Accept `filepath` as an alias for backward compatibility
    if data_path is None and filepath is not None:
        data_path = filepath
    if data_path is None:
        raise ValueError('data_path (or filepath) must be provided')

    # Resolve files
    path_a = _find_file(data_path, file_a)
    path_b = _find_file(data_path, file_b)

    print('Input datasets confirmed as WRANGLED datasets....')
    print('Reading:')
    print(' -', path_a)
    print(' -', path_b)

    df_a = _read_wrangled(path_a)
    df_b = _read_wrangled(path_b)

    # canonicalize oxide labels for columns
    df_a.columns = [canonicalize_oxide_label(c) for c in df_a.columns]
    df_b.columns = [canonicalize_oxide_label(c) for c in df_b.columns]

    # compute sizes
    area_a = df_a.shape[0] * df_a.shape[1]
    area_b = df_b.shape[0] * df_b.shape[1]

    if area_a <= area_b:
        small, large = (df_a, df_b)
        name_small, name_large = (os.path.basename(path_a), os.path.basename(path_b))
    else:
        small, large = (df_b, df_a)
        name_small, name_large = (os.path.basename(path_b), os.path.basename(path_a))

    print(f'Verifying that "{name_small}" is in "{name_large}"....')

    # Check that small's rows are subset of large's rows
    missing_rows = [r for r in small.index if r not in large.index]
    if missing_rows:
        print('DATASETS ARE NOT EQUAL! MISSING ROWS in large dataset:')
        for r in missing_rows:
            print(' -', r)
        print('DO NOT PROCEED WITH WRANGLED DATA.')
        return False, []
    else:
        print('CONFIRMED!')

    # Check that small's columns canonical are subset of large's columns
    # Build mapping from canonical name -> actual column in large (first match)
    large_canon_map = {canonicalize_oxide_label(c): c for c in large.columns}
    small_canon = [canonicalize_oxide_label(c) for c in small.columns]
    missing_cols = [c for c in small_canon if c not in large_canon_map]
    print('\nChecking for headers matches (strings) equality across datasets....')
    if missing_cols:
        print('DATASETS ARE NOT EQUAL! MISSING COLUMNS in large dataset:')
        for c in missing_cols:
            print(' -', c)
        print('DO NOT PROCEED WITH WRANGLED DATA.')
        return False, []
    else:
        print('CONFIRMED!')

    # Build column mapping small_col -> large_col
    col_map = {small.columns[i]: large_canon_map[small_canon[i]] for i in range(len(small.columns))}

    print('\nChecking for difference between similarly-indexed numeric values across datasets....')
    # Compare values
    mismatches = []
    for r in small.index:
        for sc in small.columns:
            lc = col_map[sc]
            val_s = small.at[r, sc]
            val_l = large.at[r, lc]
            # Coerce to numeric
            vs = pd.to_numeric(val_s, errors='coerce')
            vl = pd.to_numeric(val_l, errors='coerce')
            if pd.isna(vs) and pd.isna(vl):
                continue
            if pd.isna(vs) and not pd.isna(vl):
                mismatches.append((r, sc, vs, vl, None))
                if not list_all:
                    break
                else:
                    continue
            if not pd.isna(vs) and pd.isna(vl):
                mismatches.append((r, sc, vs, vl, None))
                if not list_all:
                    break
                else:
                    continue
            # both numeric
            if not np.isclose(float(vs), float(vl), atol=tol, rtol=0):
                mismatches.append((r, sc, float(vs), float(vl), float(vl - vs)))
                if not list_all:
                    break
        if mismatches and not list_all:
            break

    if mismatches:
        print('DATASETS ARE NOT EQUAL! DO NOT PROCEED WITH WRANGLED DATA.')
        if list_all:
            print(f'Found {len(mismatches)} mismatches (showing first 10):')
            for m in mismatches[:10]:
                r, sc, vs, vl, diff = m
                print(f' Row: {r}, Column: {sc} -> small={vs}  large={vl}  diff={diff}')
        else:
            r, sc, vs, vl, diff = mismatches[0]
            print('Example mismatch (first found):')
            print(f' Row: {r}, Column: {sc} -> small={vs}  large={vl} (difference={diff})')
        # optional CSV output of all mismatches
        if output_csv:
            try:
                out_rows = []
                for (r, sc, vs, vl, diff) in mismatches:
                    out_rows.append({'row': r, 'small_col': sc, 'small_val': vs, 'large_col': col_map[sc], 'large_val': vl, 'diff': diff})
                pd.DataFrame(out_rows).to_csv(output_csv, index=False)
                print(f'Wrote mismatches to {output_csv}')
            except Exception as e:
                print('Failed to write output CSV:', e)
        return False, mismatches

    print('CONFIRMED!!!')
    print('\nDATASETS ARE EQUAL! REJOICE!')
    return True, []


# Preserve programmatic comparator under a new name
compare_wrangled_detailed = compare_wrangled


def compare_wrangled(file_a: str, file_b: str, path: Optional[str] = None, filepath: Optional[str] = None) -> None:
    """Simple print-only comparator: compare_wrangled(file_a, file_b, path) or with keyword `filepath`.

    Backwards-compatible: accepts either `path` or `filepath` as the directory containing files.
    This function prints concise confirmation messages and returns None. For programmatic access use `compare_wrangled_detailed`.
    """
    # Accept filepath alias for backward compatibility
    if path is None and filepath is not None:
        path = filepath
    # resolve files
    try:
        pa = _find_file(path, file_a)
        pb = _find_file(path, file_b)
    except FileNotFoundError as e:
        print('ERROR:', e)
        print('DATASETS ARE NOT EQUAL! DO NOT PROCEED WITH WRANGLED DATA.')
        return

    # read data
    da = _read_wrangled(pa)
    db = _read_wrangled(pb)

    # canonicalize columns
    da.columns = [canonicalize_oxide_label(c) for c in da.columns]
    db.columns = [canonicalize_oxide_label(c) for c in db.columns]

    # decide which is smaller
    area_a = da.shape[0] * da.shape[1]
    area_b = db.shape[0] * db.shape[1]
    small, large = (da, db) if area_a <= area_b else (db, da)
    name_small = file_a if area_a <= area_b else file_b
    name_large = file_b if area_a <= area_b else file_a

    print('Input datasets confirmed as WRANGLED datasets....')
    print(f'Verifying that "{name_small}" is in "{name_large}"....')

    # check rows
    missing_rows = [r for r in small.index if r not in large.index]
    if missing_rows:
        print('DATASETS ARE NOT EQUAL!')
        print('Missing rows in larger dataset:')
        for r in missing_rows:
            print(' -', r)
        print('DO NOT PROCEED WITH WRANGLED DATA.')
        return
    print('CONFIRMED!')

    # check columns
    large_map = {canonicalize_oxide_label(c): c for c in large.columns}
    small_canon = [canonicalize_oxide_label(c) for c in small.columns]
    missing_cols = [c for c in small_canon if c not in large_map]
    print('\nChecking for headers matches (strings) equality across datasets....')
    if missing_cols:
        print('DATASETS ARE NOT EQUAL!')
        print('Missing columns in larger dataset:')
        for c in missing_cols:
            print(' -', c)
        print('DO NOT PROCEED WITH WRANGLED DATA.')
        return
    print('CONFIRMED!')

    # compare numeric values
    col_map = {small.columns[i]: large_map[small_canon[i]] for i in range(len(small.columns))}
    print('\nChecking for difference between similarly-indexed numeric values across datasets....')
    tol = 1e-12
    for r in small.index:
        for sc in small.columns:
            lc = col_map[sc]
            vs = pd.to_numeric(small.at[r, sc], errors='coerce')
            vl = pd.to_numeric(large.at[r, lc], errors='coerce')
            if pd.isna(vs) and pd.isna(vl):
                continue
            if pd.isna(vs) != pd.isna(vl) or (not pd.isna(vs) and not np.isclose(float(vs), float(vl), atol=tol, rtol=0)):
                print('DATASETS ARE NOT EQUAL!')
                print(f'First mismatch at Row: {r}, Column: {sc} -> small={vs} large={vl}')
                print('DO NOT PROCEED WITH WRANGLED DATA.')
                return
    print('CONFIRMED!!!')
    print('\nDATASETS ARE EQUAL! REJOICE!')
    return
