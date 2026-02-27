from pathlib import Path

import pandas as pd
import pytest

from sci_helpers.liam_input_converter import (
    LIAM_OXIDE_ROWS,
    LIAM_PARAM_ROWS,
)
from sci_helpers.liam_pressure_runner import validate_prepared_liam_csv


def _minimal_prepared_frame() -> pd.DataFrame:
    rows = list(LIAM_OXIDE_ROWS) + list(LIAM_PARAM_ROWS)
    values = []
    for row in rows:
        if row == "fO2 constraint":
            values.append("TRUE")
        elif row in {"Model", "Calculation", "fO2 buffer", "T unit", "P unit"}:
            defaults = {
                "Model": "rhyolite-MELTS_v1.0.x",
                "Calculation": "QF_P_Calc",
                "fO2 buffer": "NNO",
                "T unit": "C",
                "P unit": "MPa",
            }
            values.append(defaults[row])
        else:
            values.append(0.0)
    return pd.DataFrame({"S1": values}, index=rows)


def test_validate_prepared_liam_csv_happy_path(tmp_path: Path) -> None:
    csv_path = tmp_path / "liam_prepared.csv"
    _minimal_prepared_frame().to_csv(csv_path)
    validated = validate_prepared_liam_csv(csv_path)
    assert validated.columns.tolist() == ["S1"]
    assert "SiO2" in validated.index
    assert "T1" in validated.index


def test_validate_prepared_liam_csv_missing_required_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "liam_prepared_missing.csv"
    frame = _minimal_prepared_frame().drop(index=["T1"])
    frame.to_csv(csv_path)
    with pytest.raises(ValueError, match="missing required rows"):
        validate_prepared_liam_csv(csv_path)


def test_validate_prepared_liam_csv_rejects_blank_required_oxide(tmp_path: Path) -> None:
    csv_path = tmp_path / "liam_prepared_blank_oxide.csv"
    frame = _minimal_prepared_frame()
    frame.loc["SiO2", "S1"] = ""
    frame.to_csv(csv_path)
    with pytest.raises(ValueError, match="blank/non-numeric 'SiO2'"):
        validate_prepared_liam_csv(csv_path)


def test_validate_prepared_liam_csv_rejects_invalid_units(tmp_path: Path) -> None:
    csv_path = tmp_path / "liam_prepared_bad_units.csv"
    frame = _minimal_prepared_frame()
    frame.loc["T unit", "S1"] = "K"
    frame.to_csv(csv_path)
    with pytest.raises(ValueError, match="Invalid T unit"):
        validate_prepared_liam_csv(csv_path)


def test_validate_prepared_liam_csv_rejects_non_qf_calc(tmp_path: Path) -> None:
    csv_path = tmp_path / "liam_prepared_bad_calc.csv"
    frame = _minimal_prepared_frame()
    frame.loc["Calculation", "S1"] = "TP_Sequence"
    frame.to_csv(csv_path)
    with pytest.raises(ValueError, match="Only 'QF_P_Calc'"):
        validate_prepared_liam_csv(csv_path)
