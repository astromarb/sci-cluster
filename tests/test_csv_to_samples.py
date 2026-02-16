import os
import tempfile
import pandas as pd
import numpy as np
from csv_to_samples import csv_to_samples_list


def write_csv(df, path):
    df.to_csv(path)


def test_csv_normal_orientation(tmp_path):
    oxides = ['SiO2', 'Al2O3', 'FeO']
    data = {'S1': [60.0, 15.0, 2.0], 'S2': [61.0, 'NA', 2.1]}
    df = pd.DataFrame(data, index=oxides)
    p = tmp_path / 'normal.csv'
    write_csv(df, p)

    samples_list, ox_order, df_num = csv_to_samples_list(str(p))

    assert ox_order == oxides
    assert len(samples_list) == 2
    # sample names first
    assert samples_list[0][0] == 'S1'
    assert pytest_approx(samples_list[0][1:], [60.0, 15.0, 2.0])
    assert samples_list[1][0] == 'S2'
    # NA coerced to NaN
    assert np.isnan(samples_list[1][2])


def test_csv_transposed_orientation(tmp_path):
    oxides = ['SiO2', 'Al2O3', 'FeO']
    data = {'S1': [60.0, 15.0, 2.0], 'S2': [61.0, 'NA', 2.1]}
    df = pd.DataFrame(data, index=oxides)
    df_t = df.T
    p = tmp_path / 'transposed.csv'
    write_csv(df_t, p)

    samples_list, ox_order, df_num = csv_to_samples_list(str(p))

    assert ox_order == oxides
    assert len(samples_list) == 2
    assert samples_list[0][0] == 'S1' or samples_list[0][0] == 'S2'


# Helper assertion for approximate equality with NaNs

def pytest_approx(lst, expected):
    if len(lst) != len(expected):
        return False
    for a, b in zip(lst, expected):
        if b is None or (isinstance(b, float) and np.isnan(b)):
            if not np.isnan(a):
                return False
        else:
            if not np.isclose(float(a), float(b), atol=1e-6):
                return False
    return True
