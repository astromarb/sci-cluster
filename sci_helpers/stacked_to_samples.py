"""
Helpers to parse vertically-stacked Monte-Carlo style sheets into lists-of-lists
for notebook-friendly consumption.

Provides:
- stacked_file_to_wrangled(path, ...)
- merge_wrangled_results(results)

These thin wrappers rely on the existing `wrangle_dataframe` in `mc_wrangler`.
"""
from pathlib import Path
from typing import List, Dict, Optional
import pandas as pd
import re

from .mc_wrangler import wrangle_dataframe, DEFAULT_OXIDES


def stacked_file_to_wrangled(path: str,
                               sheet_name: Optional[str] = None,
                               anchor: str = 'SiO2',
                               oxide_names: Optional[List[str]] = None,
                               min_oxides: int = 3,
                               blank_row_tolerance: int = 50,
                               auto_name_prefix: str = 'sample_auto_',
                               write_out: Optional[str] = None,
                               skip_first_cols: int = 0,
                               auto_detect_skip: bool = False,
                               synthetic_rows: Optional[int] = None,
                               header_scan_rows: Optional[int] = None,
                               tab_name_col: int = 0) -> List[Dict]:
    """
    Read a CSV or Excel that contains vertically-stacked tables and return the wrangled datasets.

    Returns a list of dataset dicts with the same structure as `wrangle_dataframe` outputs.

    Notes:
    - For CSV files we read with `header=None` to preserve raw layout so anchor detection works robustly.
    - For Excel we read the requested sheet with `header=None` as well.
    - skip_first_cols: number of leading columns to drop (default 0). Useful when sheets have fixed
      metadata columns on the left (e.g. tab names, sample descriptors). Dropping them makes anchor
      detection operate on the tabulated region.
    - auto_detect_skip: if True, automatically detect and apply a left-column offset by locating the
      anchor in the raw sheet; overrides skip_first_cols if >0.
    """
    oxide_names = oxide_names or DEFAULT_OXIDES
    # normalization helper and precomputed normalized oxide tokens
    def _norm(s):
        return re.sub(r'[^A-Z0-9*]', '', str(s).upper())
    norm_oxes = [(ox, _norm(ox)) for ox in oxide_names]
    p = Path(path)

    # helper: detect synthetic header rows (return list of dicts with best_row, start, end)
    def _find_synthetic_headers(dframe, oxide_names_check, max_rows_scan=None):
        # scan full frame by default for stacked headers; can be limited by header_scan_rows
        if max_rows_scan is None:
            max_rows_scan = dframe.shape[0]
        else:
            max_rows_scan = min(max_rows_scan, dframe.shape[0])
        oxide_keys = [str(x).upper() for x in oxide_names_check]
        candidates = []
        explicit_rows = set()
        # If the raw dataframe has a 'Tab Names' column (first col by default), try to use it.
        # Look for non-empty entries in column 0 (or the configured tab_name_col available in outer scope)
        try:
            tab_col_series = dframe.iloc[:, 0].astype(str).fillna('').str.strip()
            for r_idx, cell in tab_col_series.items():
                if cell and cell.upper() not in ('TAB NAMES', 'TAB', 'NAMES'):
                    # look downward for a nearby row (within 12 rows) containing 'SiO2' or oxide tokens
                    for look in range(r_idx, min(r_idx + 13, max_rows_scan)):
                        row_vals_any = dframe.iloc[look, :].astype(str).fillna('').str.strip().str.upper().tolist()
                        found_anchor = False
                        for rc in row_vals_any:
                            nrc = re.sub(r'[^A-Z0-9*]', '', str(rc).upper())
                            if 'SIO2' in nrc or any(tok in nrc for tok in ('TIO2','AL2O3','FE','FE2O3','MNO','MGO','CAO','NA2O','K2O','P2O5')):
                                explicit_rows.add(look)
                                found_anchor = True
                                break
                        if found_anchor:
                            break
        except Exception:
            pass
        for r in range(max_rows_scan):
            row_vals = dframe.iloc[r, :].astype(str).fillna('').str.strip().str.upper()
            synth_count = int(row_vals.str.contains('SYNTHETIC').sum())
            iter_count = int(row_vals.str.contains('ITER').sum())
            oxide_count = sum(1 for v in row_vals if any(k in v for k in oxide_keys))
            score = synth_count * 4 + iter_count * 2 + oxide_count
            if score >= 3:
                # map headers to oxide labels
                hdr = row_vals.tolist()
                mapped = []
                # normalize header and oxide tokens for robust matching
                for nh in hdr:
                    found = None
                    for ox, nox in norm_oxes:
                        if nox and nox in nh:
                            found = ox
                            break
                    if found is None:
                        m = re.search(r'(SIO2|TIO2|AL2O3|FE2O3|FEO\*?|FEO|MNO|MGO|CAO|NA2O|K2O|P2O5|LOI|SUM)', nh)
                        if m:
                            found = m.group(1)
                    mapped.append(found)
                mapped_idxs = [i for i, m in enumerate(mapped) if m]
                if not mapped_idxs:
                    continue
                # prefer columns with 'SYNTHETIC' if present
                synth_idxs = [i for i, h in enumerate(hdr) if 'SYNTHETIC' in h and mapped[i]]
                if synth_idxs:
                    start = min(synth_idxs); end = max(synth_idxs)
                else:
                    start = min(mapped_idxs); end = max(mapped_idxs)
                candidates.append({'best_row': r, 'start': start, 'end': end, 'score': score})
        # remove candidates that are too close duplicates (within 2 rows)
        filtered = []
        for c in candidates:
            if not any(abs(c['best_row'] - f['best_row']) <= 2 for f in filtered):
                filtered.append(c)
        # ensure explicit anchor rows (SiO2 occurrences) are included
        for r in sorted(explicit_rows):
            # skip if already included nearby
            if any(abs(r - c['best_row']) <= 2 for c in candidates):
                continue
            row_vals = dframe.iloc[r, :].astype(str).fillna('').str.strip().str.upper().tolist()
            hdr = row_vals
            # map headers to oxide labels using normalized matching
            mapped = []
            for nh in hdr:
                found = None
                nnh = re.sub(r'[^A-Z0-9*]', '', str(nh).upper())
                for ox, nox in norm_oxes:
                    if nox and nox in nnh:
                        found = ox
                        break
                if found is None:
                    m = re.search(r'(SIO2|TIO2|AL2O3|FE2O3|FEO\*?|FEO|MNO|MGO|CAO|NA2O|K2O|P2O5|LOI|SUM)', nnh)
                    if m:
                        tok = m.group(1)
                        for ox, nox in norm_oxes:
                            if nox == tok:
                                found = ox
                                break
                        if found is None:
                            found = tok
                mapped.append(found)
            mapped_idxs = [i for i, m in enumerate(mapped) if m]
            if not mapped_idxs:
                continue
            synth_idxs = [i for i, h in enumerate(hdr) if 'SYNTHETIC' in h and mapped[i]]
            if synth_idxs:
                start = min(synth_idxs); end = max(synth_idxs)
            else:
                start = min(mapped_idxs); end = max(mapped_idxs)
            candidates.append({'best_row': r, 'start': start, 'end': end, 'score': 100})
        return candidates

    if p.suffix.lower() in ('.xls', '.xlsx'):
        raw_df = pd.read_excel(path, sheet_name=sheet_name, header=None)
        df = raw_df.copy()
        # detect headers using the original raw_df (so tab-name column is available)
        candidates_raw = _find_synthetic_headers(raw_df, oxide_names, max_rows_scan=header_scan_rows)
        # if auto_detect_skip is requested and no explicit skip provided, pick a candidate to set skip
        if auto_detect_skip and skip_first_cols == 0 and candidates_raw:
            candidates_raw.sort(key=lambda x: (-x['score'], x['start']))
            # choose left-most start as skip count
            skip_first_cols = candidates_raw[0]['start']

        # Optionally create working df by dropping leading metadata columns
        if skip_first_cols and raw_df.shape[1] > skip_first_cols:
            df = raw_df.iloc[:, skip_first_cols:].copy()
        else:
            df = raw_df.copy()

        # adjust candidates from raw indices to working-df indices (subtract skip_first_cols)
        candidates = []
        for c in candidates_raw:
            start_adj = c['start'] - skip_first_cols
            end_adj = c['end'] - skip_first_cols
            # skip candidates that fall entirely in the dropped metadata area
            if end_adj < 0 or start_adj >= df.shape[1]:
                continue
            candidates.append({'best_row': c['best_row'], 'start': max(0, start_adj), 'end': min(df.shape[1]-1, end_adj), 'score': c.get('score', 0)})

        # find all synthetic header candidates and extract datasets
        candidates = _find_synthetic_headers(df, oxide_names, max_rows_scan=header_scan_rows)
        datasets = []
        for cand in candidates:
            best_row = cand['best_row']; start = cand['start']; end = cand['end']
            hdr = df.iloc[best_row, start:end+1].astype(str).fillna('').tolist()
            mapped = []
            # normalize mapping similarly
            for h in hdr:
                h_up = str(h).upper()
                nh = _norm(h_up)
                found = None
                for ox, nox in norm_oxes:
                    if nox and nox in nh:
                        found = ox
                        break
                if found is None:
                    m = re.search(r'(SIO2|TIO2|AL2O3|FE2O3|FEO\*?|FEO|MNO|MGO|CAO|NA2O|K2O|P2O5|LOI|SUM)', nh)
                    if m:
                        tok = m.group(1)
                        # map back to canonical oxide name when possible
                        for ox, nox in norm_oxes:
                            if nox == tok:
                                found = ox
                                break
                        if found is None:
                            found = tok
                mapped.append(found)
            # determine number of rows to take
            if synthetic_rows and synthetic_rows > 0:
                df_block = df.iloc[best_row+1:best_row+1+synthetic_rows, start:end+1].copy()
            else:
                # take until next candidate or until non-numeric row or blank rows tolerance
                # find next candidate row > best_row
                next_rows = [c['best_row'] for c in candidates if c['best_row'] > best_row]
                next_row = min(next_rows) if next_rows else df.shape[0]
                df_block = df.iloc[best_row+1:next_row, start:end+1].copy()
            # drop trailing rows that contain summary keywords like 'MEAN' or '#DIV' in any cell
            def _is_summary_row(row):
                row_s = ' '.join([str(x).upper() for x in row.astype(str).fillna('')])
                return any(k in row_s for k in ('MEAN', 'STDEV', 'STDEV.S', 'SUM', '#DIV'))
            # trim trailing summary rows
            while not df_block.empty and _is_summary_row(df_block.iloc[-1, :]):
                df_block = df_block.iloc[:-1, :]
            # coerce columns to numeric
            df_block.columns = mapped
            try:
                df_block = df_block.apply(lambda c: pd.to_numeric(c, errors='coerce'))
            except Exception:
                pass
            samples = []
            iter_col = start-1 if start-1 >= 0 else None
            for idx in range(df_block.shape[0]):
                rowvals = df_block.iloc[idx, :].tolist()
                if iter_col is not None:
                    sample_name = str(df.iat[best_row+1+idx, iter_col])
                else:
                    sample_name = f'iter_{idx+1}'
                samples.append([sample_name] + rowvals)
            # try to fetch dataset name from the original raw dataframe (before column-skip)
            dataset_name = None
            try:
                # raw_df holds original columns; best_row is row index in df_work which matches raw_df's row index
                dataset_name = str(raw_df.iat[best_row, tab_name_col]) if raw_df is not None and tab_name_col >= 0 and tab_name_col < raw_df.shape[1] else None
            except Exception:
                dataset_name = None
            ds = {'dataset_name': dataset_name, 'samples_list': samples, 'oxides_order': mapped, 'df': df_block, 'anchor': (best_row, start), 'sheet': sheet_name, 'notes': [f'Detected synthetic header at row {best_row}, columns {start}-{end}']}
            ds['col_offset'] = skip_first_cols
            datasets.append(ds)
        # (old Excel-branch CSV writer removed; unified writer below handles both CSV and Excel branches)
        # fallback: no synthetic datasets found in Excel branch - delegate to wrangle_dataframe
        results = wrangle_dataframe(df, oxide_names=oxide_names, anchor=anchor,
                                    min_oxides=min_oxides, blank_row_tolerance=blank_row_tolerance,
                                    auto_name_prefix=auto_name_prefix, write_out=write_out)
        for ds in results:
            ds['sheet'] = sheet_name
            ds['col_offset'] = skip_first_cols
        return results
    else:
        raw_df = pd.read_csv(path, header=None, dtype=object)
        df = raw_df.copy()
        # detect headers using the existing raw_df (so tab-name column is available)
        candidates_raw = _find_synthetic_headers(raw_df, oxide_names, max_rows_scan=header_scan_rows)
        # if auto_detect_skip is requested and no explicit skip provided, pick a candidate to set skip
        if auto_detect_skip and skip_first_cols == 0 and candidates_raw:
            candidates_raw.sort(key=lambda x: (-x['score'], x['start']))
            skip_first_cols = candidates_raw[0]['start']

        # build working df by dropping leading metadata columns
        if skip_first_cols and raw_df.shape[1] > skip_first_cols:
            df = raw_df.iloc[:, skip_first_cols:].copy()
        else:
            df = raw_df.copy()

        # adjust raw candidates to working df coords
        candidates = []
        for c in candidates_raw:
            start_adj = c['start'] - skip_first_cols
            end_adj = c['end'] - skip_first_cols
            if end_adj < 0 or start_adj >= df.shape[1]:
                continue
            candidates.append({'best_row': c['best_row'], 'start': max(0, start_adj), 'end': min(df.shape[1]-1, end_adj), 'score': c.get('score', 0)})

        # find synthetic header candidates for CSV and extract datasets similarly
        candidates = _find_synthetic_headers(df, oxide_names, max_rows_scan=header_scan_rows)
        datasets = []
        for cand in candidates:
            best_row = cand['best_row']; start = cand['start']; end = cand['end']
            hdr = df.iloc[best_row, start:end+1].astype(str).fillna('').tolist()
            mapped = []
            for h in hdr:
                h_up = str(h).upper()
                nh = _norm(h_up)
                found = None
                for ox, nox in norm_oxes:
                    if nox and nox in nh:
                        found = ox
                        break
                if found is None:
                    m = re.search(r'(SIO2|TIO2|AL2O3|FE2O3|FEO\*?|FEO|MNO|MGO|CAO|NA2O|K2O|P2O5|LOI|SUM)', nh)
                    if m:
                        tok = m.group(1)
                        for ox, nox in norm_oxes:
                            if nox == tok:
                                found = ox
                                break
                        if found is None:
                            found = tok
                mapped.append(found)
            # determine block rows
            if synthetic_rows and synthetic_rows > 0:
                df_block = df.iloc[best_row+1:best_row+1+synthetic_rows, start:end+1].copy()
            else:
                next_rows = [c['best_row'] for c in candidates if c['best_row'] > best_row]
                next_row = min(next_rows) if next_rows else df.shape[0]
                df_block = df.iloc[best_row+1:next_row, start:end+1].copy()
            # drop trailing rows that contain summary keywords like 'MEAN' or '#DIV' in any cell
            def _is_summary_row(row):
                row_s = ' '.join([str(x).upper() for x in row.astype(str).fillna('')])
                return any(k in row_s for k in ('MEAN', 'STDEV', 'STDEV.S', 'SUM', '#DIV'))
            # trim trailing summary rows
            while not df_block.empty and _is_summary_row(df_block.iloc[-1, :]):
                df_block = df_block.iloc[:-1, :]
            # coerce columns to numeric
            df_block.columns = mapped
            try:
                df_block = df_block.apply(lambda c: pd.to_numeric(c, errors='coerce'))
            except Exception:
                pass
            samples = []
            iter_col = start-1 if start-1 >= 0 else None
            for idx in range(df_block.shape[0]):
                rowvals = df_block.iloc[idx, :].tolist()
                if iter_col is not None:
                    # sample name from original df (before column-skip) if possible
                    try:
                        sample_name = str(raw_df.iat[best_row+1+idx, iter_col])
                    except Exception:
                        sample_name = f'iter_{idx+1}'
                else:
                    sample_name = f'iter_{idx+1}'
                samples.append([sample_name] + rowvals)
            # fetch dataset name from raw_df tab-name column if available
            dataset_name = None
            try:
                dataset_name = str(raw_df.iat[best_row, tab_name_col]) if raw_df is not None and tab_name_col >= 0 and tab_name_col < raw_df.shape[1] else None
            except Exception:
                dataset_name = None
            ds = {'dataset_name': dataset_name, 'samples_list': samples, 'oxides_order': mapped, 'df': df_block, 'anchor': (best_row, start), 'sheet': sheet_name, 'notes': [f'Detected synthetic header at row {best_row}, columns {start}-{end}']}
            ds['col_offset'] = skip_first_cols
            datasets.append(ds)
        # if any datasets found, optionally write out per-dataset CSVs
        if datasets:
            # helper: sanitize and unique filenames
            def _sanitize_name(n):
                s = str(n) if n is not None else ''
                s = re.sub(r'\s+', '_', s)
                s = re.sub(r'[^\w\-_.]', '', s)
                s = s.strip('_')
                return s[:80] or 'dataset'

            # determine output directory
            if write_out:
                out_dir = Path(write_out)
            else:
                # default: place in sibling sci-data/wrangled-outputs if possible
                try:
                    out_dir = p.parent.parent / 'wrangled-outputs'
                except Exception:
                    out_dir = Path.cwd() / 'wrangled-outputs'
            out_dir.mkdir(parents=True, exist_ok=True)
            used = set()
            for i, ds in enumerate(datasets, start=1):
                raw_name = ds.get('dataset_name') or f'dataset_{i}'
                base = _sanitize_name(raw_name)
                fname = f'wrangled_{base}_compositions.csv'
                target = out_dir / fname
                # ensure uniqueness
                k = 1
                while target.exists() or str(target) in used:
                    target = out_dir / f'wrangled_{base}_{k}_compositions.csv'
                    k += 1
                # build df for writing
                df_block = ds.get('df')
                samples = ds.get('samples_list', [])
                try:
                    idx = [str(s[0]) for s in samples]
                    df_view = df_block.copy()
                    df_view.index = idx
                except Exception:
                    df_view = df_block.copy()
                try:
                    df_view.to_csv(target, index=True)
                    ds.setdefault('notes', []).append(f'WROTE_CSV: {str(target)}')
                    ds['written_csv'] = str(target)
                    used.add(str(target))
                except Exception as e:
                    ds.setdefault('notes', []).append(f'ERROR_WRITING_CSV: {e}')
            return datasets

        # fallback to wrangle_dataframe if nothing found in CSV branch
        results = wrangle_dataframe(df, oxide_names=oxide_names, anchor=anchor,
                                    min_oxides=min_oxides, blank_row_tolerance=blank_row_tolerance,
                                    auto_name_prefix=auto_name_prefix, write_out=write_out)
        for ds in results:
            ds['sheet'] = sheet_name
            ds['col_offset'] = skip_first_cols
        return results

