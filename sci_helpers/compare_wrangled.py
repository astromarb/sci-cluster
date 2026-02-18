"""Compare wrangled dataset files (CSV/Excel) for header and numeric equality."""

from __future__ import annotations

from typing import List, Optional, Tuple
import os

import numpy as np
import pandas as pd

from .mc_wrangler import canonicalize_oxide_label

Mismatch = Tuple[str, str, float, float, float]


def _read_wrangled(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, index_col=0)
    else:
        try:
            df = pd.read_excel(path, index_col=0)
        except Exception:
            df = pd.read_csv(path, index_col=0)
    df.index = df.index.map(lambda x: str(x).strip())
    df.columns = df.columns.map(lambda x: str(x).strip())
    return df


def _find_file(data_path: str, fname: str) -> str:
    if os.path.isabs(fname):
        if os.path.exists(fname):
            return fname
        for ext in (".csv", ".xlsx"):
            if os.path.exists(fname + ext):
                return fname + ext
        raise FileNotFoundError(f"Could not locate file {fname} (absolute path)")

    norm_fname = fname.replace("/", os.sep).replace("\\", os.sep).lstrip("." + os.sep)
    candidate = os.path.join(data_path, norm_fname)
    if os.path.exists(candidate):
        return candidate

    base, ext = os.path.splitext(candidate)
    if ext == "":
        for add_ext in (".csv", ".xlsx"):
            if os.path.exists(candidate + add_ext):
                return candidate + add_ext

    match_norm = norm_fname.replace(os.sep, "/").lower()
    for root, _, files in os.walk(data_path):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), data_path).replace("\\", "/")
            rel_low = rel.lower()
            if rel_low.endswith(match_norm) or f.lower() == os.path.basename(match_norm).lower():
                return os.path.join(root, f)
            if os.path.splitext(match_norm)[1] == "":
                if os.path.splitext(f)[0].lower() == os.path.basename(match_norm).lower():
                    return os.path.join(root, f)

    raise FileNotFoundError(f"Could not locate file {fname} in {data_path} (searched recursively)")


def compare_wrangled_detailed(
    file_a: str,
    file_b: str,
    data_path: Optional[str] = None,
    tol: float = 1e-12,
    list_all: bool = False,
    output_csv: Optional[str] = None,
    filepath: Optional[str] = None,
) -> Tuple[bool, List[Mismatch]]:
    """Programmatic comparator. Returns (equal, mismatches)."""
    if data_path is None and filepath is not None:
        data_path = filepath
    if data_path is None:
        raise ValueError("data_path (or filepath) must be provided")

    path_a = _find_file(data_path, file_a)
    path_b = _find_file(data_path, file_b)

    print("Input datasets confirmed as WRANGLED datasets....")
    print("Reading:")
    print(" -", path_a)
    print(" -", path_b)

    df_a = _read_wrangled(path_a)
    df_b = _read_wrangled(path_b)

    df_a.columns = [canonicalize_oxide_label(c) for c in df_a.columns]
    df_b.columns = [canonicalize_oxide_label(c) for c in df_b.columns]

    area_a = df_a.shape[0] * df_a.shape[1]
    area_b = df_b.shape[0] * df_b.shape[1]

    if area_a <= area_b:
        small, large = (df_a, df_b)
        name_small, name_large = (os.path.basename(path_a), os.path.basename(path_b))
    else:
        small, large = (df_b, df_a)
        name_small, name_large = (os.path.basename(path_b), os.path.basename(path_a))

    print(f'Verifying that "{name_small}" is in "{name_large}"....')

    missing_rows = [r for r in small.index if r not in large.index]
    if missing_rows:
        print("DATASETS ARE NOT EQUAL! MISSING ROWS in large dataset:")
        for r in missing_rows:
            print(" -", r)
        print("DO NOT PROCEED WITH WRANGLED DATA.")
        return False, []
    print("CONFIRMED!")

    large_canon_map = {canonicalize_oxide_label(c): c for c in large.columns}
    small_canon = [canonicalize_oxide_label(c) for c in small.columns]
    missing_cols = [c for c in small_canon if c not in large_canon_map]
    print("\nChecking for headers matches (strings) equality across datasets....")
    if missing_cols:
        print("DATASETS ARE NOT EQUAL! MISSING COLUMNS in large dataset:")
        for c in missing_cols:
            print(" -", c)
        print("DO NOT PROCEED WITH WRANGLED DATA.")
        return False, []
    print("CONFIRMED!")

    col_map = {small.columns[i]: large_canon_map[small_canon[i]] for i in range(len(small.columns))}

    print("\nChecking for difference between similarly-indexed numeric values across datasets....")
    mismatches: List[Mismatch] = []
    for r in small.index:
        for sc in small.columns:
            lc = col_map[sc]
            val_s = small.at[r, sc]
            val_l = large.at[r, lc]
            vs = pd.to_numeric(val_s, errors="coerce")
            vl = pd.to_numeric(val_l, errors="coerce")
            if pd.isna(vs) and pd.isna(vl):
                continue
            if pd.isna(vs) and not pd.isna(vl):
                mismatches.append((r, sc, vs, vl, None))
                if not list_all:
                    break
                continue
            if not pd.isna(vs) and pd.isna(vl):
                mismatches.append((r, sc, vs, vl, None))
                if not list_all:
                    break
                continue
            if not np.isclose(float(vs), float(vl), atol=tol, rtol=0):
                mismatches.append((r, sc, float(vs), float(vl), float(vl - vs)))
                if not list_all:
                    break
        if mismatches and not list_all:
            break

    if mismatches:
        print("DATASETS ARE NOT EQUAL! DO NOT PROCEED WITH WRANGLED DATA.")
        if list_all:
            print(f"Found {len(mismatches)} mismatches (showing first 10):")
            for m in mismatches[:10]:
                r, sc, vs, vl, diff = m
                print(f" Row: {r}, Column: {sc} -> small={vs}  large={vl}  diff={diff}")
        else:
            r, sc, vs, vl, diff = mismatches[0]
            print("Example mismatch (first found):")
            print(f" Row: {r}, Column: {sc} -> small={vs}  large={vl} (difference={diff})")
        if output_csv:
            try:
                out_rows = []
                for (r, sc, vs, vl, diff) in mismatches:
                    out_rows.append(
                        {
                            "row": r,
                            "small_col": sc,
                            "small_val": vs,
                            "large_col": col_map[sc],
                            "large_val": vl,
                            "diff": diff,
                        }
                    )
                pd.DataFrame(out_rows).to_csv(output_csv, index=False)
                print(f"Wrote mismatches to {output_csv}")
            except Exception as e:
                print("Failed to write output CSV:", e)
        return False, mismatches

    print("CONFIRMED!!!")
    print("\nDATASETS ARE EQUAL! REJOICE!")
    return True, []


def compare_wrangled(file_a: str, file_b: str, path: Optional[str] = None, filepath: Optional[str] = None) -> None:
    """Print-oriented convenience comparator. Returns None."""
    if path is None and filepath is not None:
        path = filepath
    try:
        compare_wrangled_detailed(file_a, file_b, data_path=path, list_all=False)
    except FileNotFoundError as exc:
        print("ERROR:", exc)
        print("DATASETS ARE NOT EQUAL! DO NOT PROCEED WITH WRANGLED DATA.")
