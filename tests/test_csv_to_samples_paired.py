import numpy as np
import pandas as pd

from sci_helpers.csv_to_samples import csv_to_samples_list


def _values_equal(a, b, atol=1e-9):
    if pd.isna(a) and pd.isna(b):
        return True
    try:
        return np.isclose(float(a), float(b), atol=atol)
    except Exception:
        return False


def compare_outputs(a, b):
    samples_a, ox_a, df_a = a
    samples_b, ox_b, df_b = b
    assert ox_a == ox_b
    assert len(samples_a) == len(samples_b)
    for sa, sb in zip(samples_a, samples_b):
        assert sa[0] == sb[0]
        vals_a = sa[1:]
        vals_b = sb[1:]
        assert len(vals_a) == len(vals_b)
        for va, vb in zip(vals_a, vals_b):
            assert _values_equal(va, vb), f"Value mismatch: {va} != {vb}"
    pd.testing.assert_frame_equal(df_a, df_b, check_dtype=False, check_like=True, check_names=False)


def test_paired_variants_same_parser(tmp_path):
    oxides = ["SiO2", "FeO*", "P2O5", "LOI"]
    data = {"S1": [65.0, 0.7, 0.01, 1.2], "S2": [66.0, 0.8, 0.02, "NA"]}
    df = pd.DataFrame(data, index=oxides)

    p = tmp_path / "paired_normal.csv"
    df.to_csv(p)
    out_normal = csv_to_samples_list(str(p))

    p_t = tmp_path / "paired_transposed.csv"
    df.T.to_csv(p_t)
    out_transposed = csv_to_samples_list(str(p_t))

    compare_outputs(out_normal, out_transposed)
