from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sci_helpers.thermoengine_geobarometer import (
    GeobarometerRunConfig,
    affinity_objective_from_affinities,
    build_phase_mu_from_con_matrix,
    compute_mu_vectors_from_liquid_potentials,
    convert_oxide_wt_to_blk_cmp,
    fit_parabola_pressure_minimum,
    load_sample_inputs_from_csv,
    run_geobarometer_driver,
)


def test_affinity_objective_from_affinities_matches_notebook_formula() -> None:
    objective = affinity_objective_from_affinities(13.0, 3.0)
    assert objective == pytest.approx(2.0)


def test_compute_mu_vectors_from_liquid_potentials_uses_expected_indices() -> None:
    mu_liq = np.arange(20.0)
    mu_fld, mu_qtz = compute_mu_vectors_from_liquid_potentials(mu_liq)
    expected_fld = np.array(
        [
            5.0 * mu_liq[0] / 2.0 + mu_liq[2] / 2.0 + mu_liq[11] / 2.0,
            mu_liq[0] + mu_liq[2] + mu_liq[10],
            2.0 * mu_liq[0] + mu_liq[12],
        ]
    )
    assert np.allclose(mu_fld, expected_fld)
    assert np.allclose(mu_qtz, np.array([mu_liq[0]]))


def test_build_phase_mu_from_con_matrix_handles_zero_liquid_components() -> None:
    con = np.array(
        [
            [1.0, 2.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    mu_liq = np.array([10.0, 20.0, 30.0])
    m_liq = np.array([1.0, 0.0, 2.0])
    out = build_phase_mu_from_con_matrix(con, mu_liq, m_liq)
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(30.0)


def test_fit_parabola_pressure_minimum_returns_expected_p_and_dp() -> None:
    pressures = np.array([150.0, 160.0, 170.0, 180.0, 190.0])
    residuals = ((pressures - 173.0) ** 2) / 9.0 + 0.5
    result = fit_parabola_pressure_minimum(pressures, residuals, delta_residual=1.0)
    assert result["success"] is True
    assert result["pressure_mpa"] == pytest.approx(173.0, abs=1e-2)
    assert result["dP_mpa"] == pytest.approx(3.0, abs=1e-2)


def test_load_sample_inputs_from_csv_applies_defaults(tmp_path: Path) -> None:
    csv_path = tmp_path / "samples.csv"
    pd.DataFrame(
        [
            {
                "sample_id": "S1",
                "SiO2": 77.5,
                "TiO2": 0.08,
                "Al2O3": 12.5,
                "Fe2O3": 0.207,
                "Cr2O3": 0.0,
                "FeO": 0.473,
                "MnO": 0.0,
                "MgO": 0.03,
                "NiO": 0.0,
                "CoO": 0.0,
                "CaO": 0.43,
                "Na2O": 3.98,
                "K2O": 4.88,
                "P2O5": 0.0,
                "H2O": 10.0,
            }
        ]
    ).to_csv(csv_path, index=False)

    samples = load_sample_inputs_from_csv(csv_path)
    assert len(samples) == 1
    s1 = samples[0]
    assert s1.sample_id == "S1"
    assert s1.fO2_offset == pytest.approx(0.0)
    assert s1.T_init_K == pytest.approx(1034.0)
    assert s1.P_init_bar == pytest.approx(1750.0)
    assert s1.P0_MPa == pytest.approx(400.0)


class _FakeChem:
    PERIODIC_ORDER = np.array(["H", "O", "Na", "Mg"])

    @staticmethod
    def format_mol_oxide_comp(_oxide_wt, convert_grams_to_moles=True):
        assert convert_grams_to_moles is True
        return {"dummy": 1.0}


class _FakeCore:
    chem = _FakeChem()


class _FakeLiquidGood:
    @staticmethod
    def calc_endmember_comp(mol_oxide_comp, method, output_residual):
        assert method == "intrinsic"
        assert output_residual is True
        assert mol_oxide_comp == {"dummy": 1.0}
        return np.array([1.0]), np.array([0.0])

    @staticmethod
    def test_endmember_comp(_moles_end):
        return True

    @staticmethod
    def convert_endmember_comp(_moles_end, output):
        assert output == "moles_elements"
        return np.array([10.0, 20.0, 30.0, 40.0])


class _FakeLiquidBad(_FakeLiquidGood):
    @staticmethod
    def test_endmember_comp(_moles_end):
        return False


def test_convert_oxide_wt_to_blk_cmp_returns_ordered_vector() -> None:
    blk = convert_oxide_wt_to_blk_cmp(
        oxide_wt={"SiO2": 1.0},
        core_module=_FakeCore(),
        liquid_phase=_FakeLiquidGood(),
        elm_sys=("H", "O", "Na", "Mg"),
    )
    assert np.allclose(blk, np.array([10.0, 20.0, 30.0, 40.0]))


def test_convert_oxide_wt_to_blk_cmp_raises_for_infeasible_composition() -> None:
    with pytest.raises(ValueError, match="infeasible"):
        convert_oxide_wt_to_blk_cmp(
            oxide_wt={"SiO2": 1.0},
            core_module=_FakeCore(),
            liquid_phase=_FakeLiquidBad(),
            elm_sys=("H", "O", "Na", "Mg"),
        )


RUN_INTEGRATION = os.environ.get("RUN_THERMOENGINE_INTEGRATION") == "1"


@pytest.mark.skipif(not RUN_INTEGRATION, reason="Set RUN_THERMOENGINE_INTEGRATION=1 to run runtime tests")
def test_integration_geobarometer_benchmark_gate(tmp_path: Path) -> None:
    csv_path = tmp_path / "integration_samples.csv"
    pd.DataFrame(
        [
            {
                "sample_id": "bench",
                "SiO2": 77.5,
                "TiO2": 0.08,
                "Al2O3": 12.5,
                "Fe2O3": 0.207,
                "Cr2O3": 0.0,
                "FeO": 0.473,
                "MnO": 0.0,
                "MgO": 0.03,
                "NiO": 0.0,
                "CoO": 0.0,
                "CaO": 0.43,
                "Na2O": 3.98,
                "K2O": 4.88,
                "P2O5": 0.0,
                "H2O": 10.0,
            }
        ]
    ).to_csv(csv_path, index=False)

    cfg = GeobarometerRunConfig(
        input_csv=csv_path,
        output_dir=tmp_path / "out",
        mode="geobarometer",
        benchmark_first=True,
        thermoengine_src_root=Path("/Users/lopezama/PycharmProjects/ThermoEngine-master/thermoengine"),
    )
    summary = run_geobarometer_driver(cfg)

    assert summary["benchmark"]["passed"] is True
    sample = summary["samples"][0]
    assert sample["status"] == "success"
    assert sample["geobarometer"]["success"] is True


@pytest.mark.skipif(not RUN_INTEGRATION, reason="Set RUN_THERMOENGINE_INTEGRATION=1 to run runtime tests")
def test_integration_pressure_root_quartz_converges(tmp_path: Path) -> None:
    csv_path = tmp_path / "integration_samples.csv"
    pd.DataFrame(
        [
            {
                "sample_id": "pressure",
                "SiO2": 77.5,
                "TiO2": 0.08,
                "Al2O3": 12.5,
                "Fe2O3": 0.207,
                "Cr2O3": 0.0,
                "FeO": 0.473,
                "MnO": 0.0,
                "MgO": 0.03,
                "NiO": 0.0,
                "CoO": 0.0,
                "CaO": 0.43,
                "Na2O": 3.98,
                "K2O": 4.88,
                "P2O5": 0.0,
                "H2O": 10.0,
                "T_init_K": 1034.0,
            }
        ]
    ).to_csv(csv_path, index=False)

    cfg = GeobarometerRunConfig(
        input_csv=csv_path,
        output_dir=tmp_path / "out_pressure",
        mode="pressure",
        pressure_bracket_bar=(1500.0, 1750.0),
        benchmark_first=False,
        thermoengine_src_root=Path("/Users/lopezama/PycharmProjects/ThermoEngine-master/thermoengine"),
    )
    summary = run_geobarometer_driver(cfg)

    sample = summary["samples"][0]
    assert sample["pressure_quartz"]["success"] is True
    assert np.isfinite(sample["pressure_quartz"]["P_bar"])


@pytest.mark.skipif(not RUN_INTEGRATION, reason="Set RUN_THERMOENGINE_INTEGRATION=1 to run runtime tests")
def test_integration_liquidus_loop_outputs_phase_curves(tmp_path: Path) -> None:
    csv_path = tmp_path / "integration_samples.csv"
    pd.DataFrame(
        [
            {
                "sample_id": "liq",
                "SiO2": 77.5,
                "TiO2": 0.08,
                "Al2O3": 12.5,
                "Fe2O3": 0.207,
                "Cr2O3": 0.0,
                "FeO": 0.473,
                "MnO": 0.0,
                "MgO": 0.03,
                "NiO": 0.0,
                "CoO": 0.0,
                "CaO": 0.43,
                "Na2O": 3.98,
                "K2O": 4.88,
                "P2O5": 0.0,
                "H2O": 10.0,
                "P0_MPa": 300.0,
                "Pf_MPa": 250.0,
                "dP_MPa": 50.0,
            }
        ]
    ).to_csv(csv_path, index=False)

    cfg = GeobarometerRunConfig(
        input_csv=csv_path,
        output_dir=tmp_path / "out_liq",
        mode="liquidus",
        benchmark_first=False,
        pressure_grid_mpa=(300.0, 250.0, 50.0),
        thermoengine_src_root=Path("/Users/lopezama/PycharmProjects/ThermoEngine-master/thermoengine"),
    )
    summary = run_geobarometer_driver(cfg)

    liquidus_csv = Path(summary["output_paths"]["liquidus_curves_csv"])
    liquidus = pd.read_csv(liquidus_csv)
    assert not liquidus.empty
    for _, frame in liquidus.groupby("phase_key"):
        pressures = frame["pressure_MPa"].to_numpy()
        assert np.all(np.diff(pressures) <= 0.0)
