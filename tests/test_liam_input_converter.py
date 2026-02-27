import pandas as pd
import pytest

from sci_helpers.liam_input_converter import (
    DEFAULT_MELTS_PARAM_ORDER,
    DEFAULT_REQUIRED_OUTPUT_OXIDES,
    build_liam_input_for_sample,
)


def _base_wrangled_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "SiO2": [76.2],
            "TiO2": [0.06],
            "Al2O3": [13.0],
            "FeO": [0.64],
            "MnO": [0.03],
            "MgO": [0.10],
            "CaO": [0.64],
            "Na2O": [4.49],
            "K2O": [3.95],
            "P2O5": [0.02],
        },
        index=["KCP-109-B"],
    )


def _base_params() -> dict:
    return {
        "Model": "rhyolite-MELTS_v1.0.x",
        "Calculation": "QF_P_Calc",
        "T1": 1100,
        "T2": 700,
        "ΔT": 1,
        "T unit": "C",
        "P1": 400,
        "P2": 50,
        "ΔP": 25,
        "P unit": "MPa",
        "fO2 offset": 0,
        "fO2 buffer": "NNO",
        "fO2 constraint": True,
        "ΔH": 0.5,
        "ΔV": 0,
        "ΔS": 0,
    }


def test_single_sample_conversion_order(tmp_path):
    wrangled_path = tmp_path / "wrangled.csv"
    _base_wrangled_df().to_csv(wrangled_path)

    df = build_liam_input_for_sample(
        wrangled_csv=wrangled_path,
        sample_id="KCP-109-B",
        melts_params=_base_params(),
        fixed_oxide_overrides={"H2O": 13.0},
    )

    assert df.columns.tolist() == ["Parameter", "KCP-109-B"]
    assert df["Parameter"].tolist()[: len(DEFAULT_REQUIRED_OUTPUT_OXIDES)] == DEFAULT_REQUIRED_OUTPUT_OXIDES
    assert df["Parameter"].tolist()[-len(DEFAULT_MELTS_PARAM_ORDER) :] == DEFAULT_MELTS_PARAM_ORDER

    h2o_value = df.loc[df["Parameter"] == "H2O", "KCP-109-B"].iloc[0]
    assert h2o_value == pytest.approx(13.0)


def test_missing_required_param_raises(tmp_path):
    wrangled_path = tmp_path / "wrangled.csv"
    _base_wrangled_df().to_csv(wrangled_path)

    bad_params = _base_params()
    del bad_params["T1"]

    with pytest.raises(ValueError, match="Missing required MELTS params"):
        build_liam_input_for_sample(
            wrangled_csv=wrangled_path,
            sample_id="KCP-109-B",
            melts_params=bad_params,
        )


def test_missing_required_dataset_oxide_raises(tmp_path):
    wrangled_path = tmp_path / "wrangled.csv"
    df = _base_wrangled_df().drop(columns=["CaO"])
    df.to_csv(wrangled_path)

    with pytest.raises(ValueError, match="missing required oxides"):
        build_liam_input_for_sample(
            wrangled_csv=wrangled_path,
            sample_id="KCP-109-B",
            melts_params=_base_params(),
            required_dataset_oxides=["SiO2", "CaO", "Al2O3"],
        )


def test_boolean_param_serializes_true_false(tmp_path):
    wrangled_path = tmp_path / "wrangled.csv"
    _base_wrangled_df().to_csv(wrangled_path)

    params = _base_params()
    params["fO2 constraint"] = False

    out = build_liam_input_for_sample(
        wrangled_csv=wrangled_path,
        sample_id="KCP-109-B",
        melts_params=params,
    )
    serialized = out.loc[out["Parameter"] == "fO2 constraint", "KCP-109-B"].iloc[0]
    assert serialized == "FALSE"

