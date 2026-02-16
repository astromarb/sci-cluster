"""
MC wrangler/parser for vertically-stacked Monte Carlo synthetic aplite tables.

Notebook-friendly API:
- wrangle_dataframe(df, ...)
- wrangle_excel(path, sheet_name=None, ...)

The parser looks for 'SiO2' anchors and extracts local tables by inferring oxide header
direction and sample direction. Returns a list of dataset dicts with keys:
- samples_list (list of [sample_name, v1, v2, ...])
- oxides_order
- df (DataFrame slice, numeric coercion)
- anchor (row, col)
- sheet
- notes

Defaults are tuned for standard petrological format: oxide names as row headers and
sample identifiers as column headers. The function is configurable for blank-row
tolerance, minimum oxides, and oxide name list.
"""
from typing import List, Tuple, Optional, Dict
import pandas as pd
import numpy as np
import os
import re

DEFAULT_OXIDES = ['SiO2','TiO2','Al2O3','FeO','Fe2O3','MnO','MgO','CaO','Na2O','K2O','P2O5','LOI','SUM']


def _normalize_label(s: str) -> str:
    """Normalize a label to uppercase alphanumeric only string for matching."""
    if s is None:
        return ''
    return re.sub(r'[^A-Z0-9]', '', str(s).upper())


def canonicalize_oxide_label(s: str, oxide_names: List[str] = None) -> str:
    """Return a canonical oxide label by matching against known oxide_names (case-insensitive,
    tolerant to '*' and punctuation). If no match, return the cleaned label.
    """
    if s is None:
        return s
    oxide_names = oxide_names or DEFAULT_OXIDES
    s_str = str(s).strip()
    if s_str == '':
        return s_str
    key = _normalize_label(s_str)
    for ox in oxide_names:
        if _normalize_label(ox) == key:
            return ox
    # fallback: remove common noise like '*' and return trimmed
    return s_str.replace('*', '').strip()


def _cell_eq(s, target: str) -> bool:
    try:
        return str(s).strip().lower() == target.strip().lower()
    except Exception:
        return False


def find_anchor_positions(df: pd.DataFrame, anchor: str = 'SiO2') -> List[Tuple[int,int]]:
    """Return list of (row_index, col_index) positions where anchor string appears in df values or index/header.
    Note: returns positions in the DataFrame values coordinate system (iloc indices).
    """
    positions = []
    # check index labels
    for i, lab in enumerate(df.index):
        if _cell_eq(lab, anchor):
            # anchor located in the index column at row i, column 0 (we'll use col -1 to indicate index?)
            # Represent anchor in cell coordinates by checking if index label equals anchor and if first data column exists
            positions.append((i, -1))
    # check columns
    for j, lab in enumerate(df.columns):
        if _cell_eq(lab, anchor):
            positions.append((-1, j))
    # check values
    for i in range(df.shape[0]):
        for j in range(df.shape[1]):
            if _cell_eq(df.iat[i,j], anchor):
                positions.append((i,j))
    # deduplicate
    unique = []
    for p in positions:
        if p not in unique:
            unique.append(p)
    return unique


def _is_oxide_label(s: str, oxide_names: List[str]) -> bool:
    if s is None:
        return False
    try:
        s2 = str(s).strip()
    except Exception:
        return False
    if s2 == '':
        return False
    for ox in oxide_names:
        if s2.lower() == ox.lower():
            return True
    return False


def _numeric_fraction_of_cells(values: List) -> float:
    coerced = pd.to_numeric(pd.Series(values).astype(object), errors='coerce')
    if len(coerced) == 0:
        return 0.0
    return float(coerced.notna().mean())


def detect_orientation_at_anchor(df: pd.DataFrame, r: int, c: int, oxide_names: List[str]) -> Tuple[Tuple[int,int], Tuple[int,int], List[str], List[str], List[str]]:
    """Given an anchor position (r,c) in iloc coordinates, attempt to detect
    oxide direction and sample direction.

    Returns (oxide_dir, sample_dir, oxide_labels, sample_labels, notes)
    where oxide_dir and sample_dir are (dr,dc) step vectors.
    If detection fails, defaults to oxide_dir=(1,0) (downwards) and sample_dir=(0,1).
    """
    notes = []
    # If anchor is in index (c==-1) or columns (r==-1) handle simply: prefer standard format
    if c == -1:
        # index label equals anchor; oxides are in index -> oxide_dir = (1,0) meaningless; we'll return oxide labels from index
        oxide_labels = [str(x) for x in df.index if _is_oxide_label(x, oxide_names)]
        sample_labels = [str(x) for x in df.columns]
        notes.append('Anchor found in index labels; assuming standard format (index=oxides, columns=samples).')
        return (1,0), (0,1), oxide_labels, sample_labels, notes
    if r == -1:
        # anchor in columns
        oxide_labels = [str(x) for x in df.columns if _is_oxide_label(x, oxide_names)]
        sample_labels = [str(x) for x in df.index]
        notes.append('Anchor found in column labels; assuming transposed format (columns=oxides, index=samples).')
        return (0,1), (1,0), oxide_labels, sample_labels, notes

    # general case: anchor found inside values at (r,c)
    # Look for oxide labels in 4 straight directions: down, up, right, left
    directions = {'down':(1,0),'up':(-1,0),'right':(0,1),'left':(0,-1)}
    match_counts = {}
    match_labels = {}
    for name, (dr,dc) in directions.items():
        labels = []
        i = 0
        while True:
            rr = r + (i+1)*dr
            cc = c + (i+1)*dc
            if rr < 0 or rr >= df.shape[0] or cc < 0 or cc >= df.shape[1]:
                break
            val = df.iat[rr, cc]
            if _is_oxide_label(val, oxide_names):
                labels.append(str(val).strip())
                i += 1
                continue
            else:
                break
        match_counts[name] = len(labels)
        match_labels[name] = labels

    # pick direction with max matches
    best_dir = max(match_counts.items(), key=lambda x: x[1])[0]
    best_count = match_counts[best_dir]
    oxide_labels = match_labels[best_dir]

    # if we found oxide labels downward/upward, sample direction is perpendicular
    if best_dir in ('down','up'):
        oxide_dir = directions[best_dir]
        # choose sample_dir as right if possible else left
        sample_dir = (0,1) if c+1 < df.shape[1] else (0,-1)
    else:
        oxide_dir = directions[best_dir]
        sample_dir = (1,0) if r+1 < df.shape[0] else (-1,0)

    # If no oxide labels detected (best_count == 0), try numeric-fraction heuristic: compare orientation cur vs transposed
    if best_count == 0:
        # try building small windows and check numeric fraction
        window_size = 8
        r0 = max(0, r - 1)
        c0 = max(0, c - 1)
        sub = df.iloc[r0:r0+window_size, c0:c0+window_size]
        cur_frac = _numeric_fraction_of_cells(sub.values.ravel().tolist())
        trans_frac = _numeric_fraction_of_cells(sub.T.values.ravel().tolist())
        if trans_frac > cur_frac + 0.05:
            # prefer transposed: oxide_dir = (0,1)
            oxide_dir = (0,1)
            sample_dir = (1,0)
            notes.append('No oxide labels found near anchor; orientation chosen by numeric fraction (transposed preferred).')
        else:
            oxide_dir = (1,0)
            sample_dir = (0,1)
            notes.append('No oxide labels found near anchor; default orientation chosen (oxides down, samples right).')
        # try to guess oxide labels by reading strings along oxide_dir
        labels = []
        i = 0
        while True:
            rr = r + (i+1)*oxide_dir[0]
            cc = c + (i+1)*oxide_dir[1]
            if rr < 0 or rr >= df.shape[0] or cc < 0 or cc >= df.shape[1]:
                break
            val = df.iat[rr,cc]
            if isinstance(val, str) and val.strip() != '':
                labels.append(val.strip())
                i += 1
                continue
            else:
                break
        oxide_labels = labels

    return oxide_dir, sample_dir, oxide_labels, [], notes


def extract_dataset_from_anchor(df: pd.DataFrame, r: int, c: int, oxide_dir: Tuple[int,int], sample_dir: Tuple[int,int], oxide_names: List[str], min_oxides: int = 3, blank_row_tolerance: int = 50, auto_name_prefix: str = 'sample_auto_', anchor: str = 'SiO2') -> Dict:
    """Extract a table starting at anchor (r,c) given oxide and sample directions.

    Returns dataset dict as described.
    """
    notes = []
    # Collect oxide labels by scanning along oxide_dir starting from anchor
    ox_labels = []
    i = 0
    while True:
        rr = r + (i+1)*oxide_dir[0]
        cc = c + (i+1)*oxide_dir[1]
        if rr < 0 or rr >= df.shape[0] or cc < 0 or cc >= df.shape[1]:
            break
        val = df.iat[rr, cc]
        if _is_oxide_label(val, oxide_names) or (isinstance(val, str) and val.strip()!=''):
            ox_labels.append(str(val).strip())
            i += 1
            continue
        else:
            break
    if len(ox_labels) < min_oxides:
        notes.append(f'Found only {len(ox_labels)} oxide labels at anchor; aborting extraction.')
        return {'samples_list':[], 'oxides_order':[], 'df':pd.DataFrame(), 'anchor':(r,c), 'sheet':None, 'notes':notes}

    # Now iterate samples along sample_dir; for each sample gather values for ox_labels
    samples = []
    sample_index = 0
    auto_index = 1
    consecutive_blank_samples = 0
    # sample_index = 0 corresponds to the first sample one step away from the anchor
    while True:
        sample_offset = sample_index + 1
        # compute nominal sample header cell (one step per sample_offset from anchor)
        sample_row = r + sample_offset * sample_dir[0]
        sample_col = c + sample_offset * sample_dir[1]

        # If sample position is outside bounds, stop
        if not (0 <= sample_row < df.shape[0] and 0 <= sample_col < df.shape[1]):
            # If we are out of bounds to the right/bottom, treat as termination
            notes.append('Sample position out of bounds; stopping extraction at offset {0}.'.format(sample_offset))
            break

        # Candidate header positions
        candidates = []
        # 1) header row/col at one cell before first oxide value along oxide_dir
        # For horizontal samples (common case): header likely at row r-1, column = c + sample_offset
        if sample_dir[0] == 0 and sample_dir[1] != 0:
            # header row above anchor
            candidates.append((r - 1, c + sample_offset * sample_dir[1]))
            # header directly at sample cell (if the file uses a separate label row)
            candidates.append((r, c + sample_offset * sample_dir[1]))
        elif sample_dir[1] == 0 and sample_dir[0] != 0:
            # vertical samples: header likely in first column to left
            candidates.append((r + sample_offset * sample_dir[0], c - 1))
            candidates.append((r + sample_offset * sample_dir[0], c))
        else:
            candidates.append((r, c + sample_offset * sample_dir[1]))

        # pick first non-empty string candidate that is not equal to anchor or generic 'Sample Name'
        sample_name = None
        for (hr, hc) in candidates:
            if 0 <= hr < df.shape[0] and 0 <= hc < df.shape[1]:
                v = df.iat[hr, hc]
                if isinstance(v, str) and v.strip() != '' and not _cell_eq(v, 'Sample Name') and not _cell_eq(v, anchor):
                    sample_name = v.strip()
                    break

        # Gather oxide values for this sample_offset
        values = []
        numeric_count = 0
        for k, ox in enumerate(ox_labels):
            vr = r + (k+1)*oxide_dir[0] + sample_offset*sample_dir[0]
            vc = c + (k+1)*oxide_dir[1] + sample_offset*sample_dir[1]
            if vr < 0 or vr >= df.shape[0] or vc < 0 or vc >= df.shape[1]:
                v = np.nan
            else:
                v = df.iat[vr, vc]
            try:
                vn = float(v)
                values.append(vn)
                numeric_count += 1
            except Exception:
                try:
                    vn = pd.to_numeric(v, errors='coerce')
                    if pd.notna(vn):
                        values.append(float(vn))
                        numeric_count += 1
                    else:
                        values.append(np.nan)
                except Exception:
                    values.append(np.nan)

        # Decide whether to accept or skip
        if sample_name is None:
            if numeric_count >= min_oxides:
                sample_name = f"{auto_name_prefix}{auto_index}"
                auto_index += 1
                notes.append(f'Auto-generated sample name "{sample_name}" at offset {sample_offset} with {numeric_count} numeric oxide values.')
            else:
                consecutive_blank_samples += 1
                if consecutive_blank_samples >= blank_row_tolerance:
                    notes.append(f'Encountered {consecutive_blank_samples} consecutive blank/insufficient samples; stopping.')
                    break
                else:
                    sample_index += 1
                    continue
        else:
            consecutive_blank_samples = 0

        if numeric_count < min_oxides:
            notes.append(f'Skipping sample "{sample_name}" due to insufficient numeric oxide values ({numeric_count} < {min_oxides}).')
            sample_index += 1
            continue

        samples.append([sample_name] + values)
        sample_index += 1
        # safety limit to prevent infinite loop
        if sample_index > max(df.shape[0], df.shape[1]) + 10:
            notes.append('Safety stop: sample index exceeded reasonable bounds.')
            break

    # Build a DataFrame for the extracted block (canonicalize oxide column labels)
    if len(samples) == 0:
        notes.append('No samples extracted from anchor.')
        return {'samples_list':[], 'oxides_order':[], 'df':pd.DataFrame(), 'anchor':(r,c), 'sheet':None, 'notes':notes}

    ox_order = [canonicalize_oxide_label(x, oxide_names) for x in ox_labels]
    df_block = pd.DataFrame([row[1:] for row in samples], index=[row[0] for row in samples], columns=ox_order)
    # coerce to numeric
    df_block = df_block.apply(lambda col: pd.to_numeric(col, errors='coerce'))

    return {'samples_list':samples, 'oxides_order':ox_order, 'df':df_block, 'anchor':(r,c), 'sheet':None, 'notes':notes}


def wrangle_dataframe(df: pd.DataFrame, oxide_names: Optional[List[str]] = None, anchor: str = 'SiO2', min_oxides: int = 3, blank_row_tolerance: int = 50, auto_name_prefix: str = 'sample_auto_', write_out: Optional[str] = None) -> List[Dict]:
    """Top-level parser that operates on a pandas DataFrame (single sheet).

    Parameters:
        df: pandas DataFrame (header=None recommended) containing the sheet to parse.
        oxide_names: optional list of oxide label strings to recognize (defaults to majors + LOI + SUM).
        anchor: anchor string to locate table starts (defaults to 'SiO2').
        min_oxides: minimum number of oxide labels required to accept a table (default 3).
        blank_row_tolerance: number of consecutive blank/insufficient samples that signal the end of a table (default 50).
        auto_name_prefix: prefix to use when auto-generating sample names for unlabeled columns/rows.
        write_out: optional output path. If None, no files are written.
            - If `write_out` is a directory path, each dataset is written as CSV: `wrangled_dataset_{i}.csv`.
            - If `write_out` ends with `.xlsx`, all datasets are written into a single Excel workbook (one sheet per dataset).

    Returns:
        A list of dataset dicts. Each dict contains keys: 'samples_list', 'oxides_order', 'df', 'anchor', 'sheet', 'notes'.
    """
    oxide_names = oxide_names or DEFAULT_OXIDES
    results = []
    df_work = df.copy()
    # Find anchors
    anchors = find_anchor_positions(df_work, anchor=anchor)
    if len(anchors) == 0:
        # Also check if anchor present in index (index values)
        if any(_cell_eq(x, anchor) for x in df_work.index):
            anchors.append((list(df_work.index).index(next(x for x in df_work.index if _cell_eq(x, anchor))), -1))
    if len(anchors) == 0:
        raise ValueError('No anchor positions found for "{0}"'.format(anchor))

    seen_rows = set()
    for (r, c) in anchors:
        # If anchor corresponds to index or column labels, handle directly
        oxide_dir, sample_dir, oxide_labels, sample_labels, notes = detect_orientation_at_anchor(df_work, r, c, oxide_names)
        ds = extract_dataset_from_anchor(
            df_work,
            r,
            c,
            oxide_dir,
            sample_dir,
            oxide_names,
            min_oxides=min_oxides,
            blank_row_tolerance=blank_row_tolerance,
            auto_name_prefix=auto_name_prefix,
            anchor=anchor,
        )
        ds['sheet'] = None
        ds['notes'] = ds.get('notes', []) + notes
        if ds['samples_list']:
            results.append(ds)
            # mark rows (approx) as seen to avoid re-processing nearby anchors; simple heuristic
            seen_rows.add(r)
    # Optionally write out datasets
    if write_out:
        # If write_out points to a .xlsx file, write all datasets to one workbook (one sheet each)
        if str(write_out).lower().endswith('.xlsx'):
            os.makedirs(os.path.dirname(write_out) or '.', exist_ok=True)
            with pd.ExcelWriter(write_out, engine='openpyxl') as writer:
                for i, ds in enumerate(results, start=1):
                    sheet_name = f'dataset_{i}'
                    # ensure sheet_name <= 31 chars
                    writer_name = sheet_name[:31]
                    try:
                        ds['df'].to_excel(writer, sheet_name=writer_name)
                    except Exception:
                        # fallback: write CSV files if Excel writing fails
                        out_dir = os.path.dirname(write_out) or '.'
                        fname = os.path.join(out_dir, f'wrangled_dataset_{i}.csv')
                        ds['df'].to_csv(fname)
        else:
            os.makedirs(write_out, exist_ok=True)
            for i, ds in enumerate(results, start=1):
                fname = os.path.join(write_out, f'wrangled_dataset_{i}.csv')
                ds['df'].to_csv(fname)
    return results


def wrangle_excel(path: str, sheet_name: Optional[str] = None, **kwargs) -> List[Dict]:
    """Load a single sheet from Excel and call wrangle_dataframe. Returns list of dataset dicts.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_excel(path, sheet_name=sheet_name, header=None)
    # The file may have headers in rows; keep header=None so we treat everything as cells and let parser detect
    return wrangle_dataframe(df, **kwargs)
