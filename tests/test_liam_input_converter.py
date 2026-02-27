from pathlib import Path

import pandas as pd
import pytest

from sci_helpers.liam_input_converter import (
    LIAM_OXIDE_ROWS,
    LIAM_REQUIRED_MELTS_OXIDES,
    ZERO_EPSILON,
    build_sheet_style_input_view,
    build_liam_input_for_sample,
)


def _write_wrangled_csv(path: Path) -> None:
    pd.DataFrame(
        {
            "sample_id": ["S1"],
            "SiO2": [75.8],
            "TiO2": [0.08],
            "Al2O3": [12.9],
            "Fe2O3": [0.0],
            "FeO": [0.49],
            "MnO": [0.03],
            "MgO": [0.09],
            "CaO": [0.70],
            "Na2O": [4.37],
            "K2O": [4.01],
            "P2O5": [0.01],
        }
    ).to_csv(path, index=False)


def _melts_params() -> dict[str, object]:
    return {
        "Model": "rhyolite-MELTS_v1.0.x",
        "Calculation": "QF_P_Calc",
        "T1": 1100,
        "T2": 700,
        "ΔT": 1,
        "P1": 400,
        "P2": 50,
        "ΔP": 25,
        "fO2 offset": 0,
        "fO2 buffer": "NNO",
        "fO2 constraint": True,
    }


def test_build_liam_input_for_sample_happy_path(tmp_path: Path) -> None:
    wrangled_csv = tmp_path / "wrangled.csv"
    _write_wrangled_csv(wrangled_csv)

    table = build_liam_input_for_sample(
        wrangled_csv=wrangled_csv,
        sample_id="S1",
        melts_params=_melts_params(),
        fixed_oxide_overrides={"H2O": 13.0},
    )

    assert table.columns.tolist() == ["S1"]
    assert table.index.tolist()[0:15] == list(LIAM_OXIDE_ROWS[:15])
    assert table.loc["SiO2", "S1"] == pytest.approx(75.8)
    assert table.loc["H2O", "S1"] == pytest.approx(13.0)
    assert table.loc["Fe2O3", "S1"] == pytest.approx(ZERO_EPSILON)
    assert table.loc["Cr2O3", "S1"] == pytest.approx(ZERO_EPSILON)
    assert table.loc["NiO", "S1"] == pytest.approx(ZERO_EPSILON)
    assert pd.isna(table.loc["CO2", "S1"])
    assert table.loc["fO2 constraint", "S1"] == "TRUE"

    for oxide in LIAM_REQUIRED_MELTS_OXIDES:
        assert pd.notna(table.loc[oxide, "S1"])


def test_build_liam_input_for_sample_missing_required_oxide(tmp_path: Path) -> None:
    wrangled_csv = tmp_path / "wrangled.csv"
    _write_wrangled_csv(wrangled_csv)
    frame = pd.read_csv(wrangled_csv)
    frame = frame.drop(columns=["MgO"])
    frame.to_csv(wrangled_csv, index=False)

    with pytest.raises(ValueError, match="missing required oxides"):
        build_liam_input_for_sample(
            wrangled_csv=wrangled_csv,
            sample_id="S1",
            melts_params=_melts_params(),
            fixed_oxide_overrides={"H2O": 13.0},
        )


def test_f2o_alias_and_sheet_style_view(tmp_path: Path) -> None:
    wrangled_csv = tmp_path / "wrangled.csv"
    _write_wrangled_csv(wrangled_csv)

    table = build_liam_input_for_sample(
        wrangled_csv=wrangled_csv,
        sample_id="S1",
        melts_params=_melts_params(),
        fixed_oxide_overrides={"H2O": 13.0, "F2O-1": 0.25},
    )

    assert "F2O -1" in table.index
    assert table.loc["F2O -1", "S1"] == pytest.approx(0.25)

    sheet = build_sheet_style_input_view(table)
    assert sheet.iat[0, 0] == "SiO2"
    assert sheet.iat[0, 3] == "T1 (C)"
    assert sheet.iat[0, 6] == "Model"


def test_parameter_aliases_are_canonicalized(tmp_path: Path) -> None:
    wrangled_csv = tmp_path / "wrangled.csv"
    _write_wrangled_csv(wrangled_csv)

    params = _melts_params()
    params["Model"] = "1.0.2"
    params["Calculation"] = "qf_p_calc"
    params["T unit"] = "°C"
    params["P unit"] = "mpa"

    table = build_liam_input_for_sample(
        wrangled_csv=wrangled_csv,
        sample_id="S1",
        melts_params=params,
        fixed_oxide_overrides={"H2O": 13.0},
    )

    assert table.loc["Model", "S1"] == "rhyolite-MELTS_v1.0.x"
    assert table.loc["Calculation", "S1"] == "QF_P_Calc"
    assert table.loc["T unit", "S1"] == "C"
    assert table.loc["P unit", "S1"] == "MPa"
