import pandas as pd
import numpy as np
from sci_helpers.mc_wrangler import (
    ZERO_EPSILON,
    _replace_true_zeros_with_epsilon,
    wrangle_dataframe,
)


def make_simple_table():
    # oxides as rows, samples as columns (standard)
    ox = ['SiO2','TiO2','Al2O3','FeO']
    data = {
        'Sample_1':[60.0, 0.1, 13.0, 1.0],
        'Sample_2':[61.0, 0.2, 12.8, 0.9]
    }
    df = pd.DataFrame(data, index=ox)
    # as read with header=None the DataFrame would have oxides in first column and samples as header row
    # create a headerless DF matrix similar to read_excel(header=None)
    mat = [['','Sample_1','Sample_2'], ['SiO2',60.0,61.0], ['TiO2',0.1,0.2], ['Al2O3',13.0,12.8], ['FeO',1.0,0.9]]
    df2 = pd.DataFrame(mat)
    return df2


def test_wrangle_simple_table():
    df = make_simple_table()
    results = wrangle_dataframe(df)
    assert isinstance(results, list)
    assert len(results) >= 1
    ds = results[0]
    assert 'samples_list' in ds and 'oxides_order' in ds and 'df' in ds
    # check we found SiO2
    assert any('SiO2' in ox for ox in ds['oxides_order']) or 'SiO2' in ds['df'].columns or 'SiO2' in ds['df'].index


def test_wrangle_transposed_table():
    # create a transposed version where samples are rows and oxides columns
    mat = [['','SiO2','TiO2','Al2O3','FeO'], ['Sample_1',60.0,0.1,13.0,1.0], ['Sample_2',61.0,0.2,12.8,0.9]]
    df2 = pd.DataFrame(mat)
    results = wrangle_dataframe(df2)
    assert len(results) >= 1


def test_wrangle_preserves_blank_and_maps_true_zero():
    df = pd.DataFrame(
        {
            "A": [0.0, 1.0, np.nan],
            "B": [2.0, 0.0, np.nan],
        }
    )
    out = _replace_true_zeros_with_epsilon(df)
    assert out.loc[0, "A"] == ZERO_EPSILON
    assert out.loc[1, "B"] == ZERO_EPSILON
    assert pd.isna(out.loc[2, "A"])
