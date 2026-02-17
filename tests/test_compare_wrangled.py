import os
import pandas as pd
import numpy as np
from sci_helpers.compare_wrangled import compare_wrangled_detailed


def _write_df(path, df):
    df.to_csv(path)


def test_compare_equal_basename(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    df = pd.DataFrame({'TiO2':[1.0,2.0],'Al2O3':[3.0,4.0]}, index=['s1','s2'])
    p = d / 'a.csv'
    df.to_csv(p)
    ok, mismatches = compare_wrangled_detailed('a.csv', 'a.csv', data_path=str(d))
    assert ok is True
    assert mismatches == []


def test_compare_relative_subfolder(tmp_path):
    base = tmp_path / 'sci-data'
    sub = base / 'sub'
    sub.mkdir(parents=True)
    df = pd.DataFrame({'TiO2':[1.0],'Al2O3':[3.0]}, index=['s1'])
    p = sub / 'b.csv'
    df.to_csv(p)
    ok, mismatches = compare_wrangled_detailed('sub/b.csv', 'sub/b.csv', data_path=str(base))
    assert ok is True


def test_compare_absolute_path(tmp_path):
    d = tmp_path / 'data'
    d.mkdir()
    df = pd.DataFrame({'TiO2':[1.0],'Al2O3':[3.0]}, index=['s1'])
    p = d / 'c.csv'
    df.to_csv(p)
    ok, mismatches = compare_wrangled_detailed(str(p), str(p), data_path=str(d))
    assert ok is True


def test_mismatch_detection(tmp_path):
    d = tmp_path / 'data'
    d.mkdir()
    df1 = pd.DataFrame({'TiO2':[1.0],'Al2O3':[3.0]}, index=['s1'])
    df2 = pd.DataFrame({'TiO2':[1.1],'Al2O3':[3.0]}, index=['s1'])
    p1 = d / 'd1.csv'
    p2 = d / 'd2.csv'
    df1.to_csv(p1)
    df2.to_csv(p2)
    ok, mismatches = compare_wrangled_detailed('d1.csv','d2.csv', data_path=str(d), list_all=True)
    assert ok is False
    assert len(mismatches) >= 1
