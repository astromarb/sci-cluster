from __future__ import annotations

from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
openpyxl = pytest.importorskip("openpyxl")

import rmelts_mc_pipeline as rmp


def _minimal_prepared_df():
    row = {c: 0.0 for c in rmp.MELTS_OXIDE_ROWS}
    row.update(
        {
            "sample_label": "S1",
            "SiO2": 75.0,
            "TiO2": 0.05,
            "Al2O3": 13.0,
            "Fe2O3": 0.2,
            "FeO": 0.5,
            "MnO": 0.03,
            "MgO": 0.08,
            "CaO": 0.6,
            "Na2O": 4.2,
            "K2O": 4.0,
            "P2O5": 0.01,
            "H2O": 2.0,
        }
    )
    return pd.DataFrame([row])[rmp.PREPARED_MC_COLUMNS]


def test_mc_to_csv_rmelts_converts_and_splits_iron(tmp_path):
    mc_df = pd.DataFrame(
        [
            {
                "SampleID": "dup",
                "SiO2": 75.0,
                "TiO2": 0.05,
                "Al2O3": 13.0,
                "FeOt": 1.0,
                "MnO": 0.03,
                "MgO": 0.08,
                "CaO": 0.6,
                "Na2O": 4.2,
                "K2O": 4.0,
                "P2O5": 0.01,
                "H2O": 2.0,
            },
            {
                "SampleID": "dup",
                "SiO2": 74.8,
                "TiO2": 0.04,
                "Al2O3": 13.1,
                "FeOt": 0.9,
                "MnO": 0.03,
                "MgO": 0.07,
                "CaO": 0.55,
                "Na2O": 4.1,
                "K2O": 4.2,
                "P2O5": 0.01,
                "H2O": 2.0,
            },
        ]
    )
    mc_path = tmp_path / "mc.csv"
    mc_df.to_csv(mc_path, index=False)

    result = rmp.MC_to_csv_rMELTS(
        mc_path,
        output_dir=tmp_path,
        dataset_name="demo dataset",
        sample_id_col="SampleID",
        fe_total_col="FeOt",
        fe3fet=0.2,
        validate_only=True,
    )

    prepared = pd.read_csv(result.prepared_mc_csv_path)
    qc = pd.read_csv(result.qc_report_csv_path)

    assert result.num_samples == 2
    assert prepared["sample_label"].tolist() == ["dup", "dup__2"]
    feo_expected = 1.0 * (1.0 - 0.2)
    fe2o3_expected = 1.0 * 0.2 * (rmp.M_FE2O3 / (2.0 * rmp.M_FEO))
    row0 = prepared.iloc[0]
    assert row0["FeO"] == pytest.approx(feo_expected, rel=1e-12)
    assert row0["Fe2O3"] == pytest.approx(fe2o3_expected, rel=1e-12)
    assert "FeOt_original" in qc.columns
    assert set(prepared.columns) == set(rmp.PREPARED_MC_COLUMNS)


def test_mc_to_csv_rmelts_fe3fet_bounds(tmp_path):
    mc_df = pd.DataFrame([{"SiO2": 75, "TiO2": 0.05, "Al2O3": 13, "FeOt": 1, "MnO": 0.03, "MgO": 0.1, "CaO": 0.5, "Na2O": 4, "K2O": 4, "P2O5": 0.01, "H2O": 2}])
    mc_path = tmp_path / "mc.csv"
    mc_df.to_csv(mc_path, index=False)
    with pytest.raises(ValueError):
        rmp.MC_to_csv_rMELTS(mc_path, output_dir=tmp_path, dataset_name="x", fe3fet=1.2)


def test_build_melts_input_wide_dataframe_row_order_and_labels():
    prepared_df = _minimal_prepared_df()
    params = rmp.MELTSRunParams(
        T1=1100, T2=700, dT=1, P1=400, P2=10, dP=10,
        fO2_constraint="buffered", fO2_buffer="NNO", fO2_offset=0.0
    )
    wide = rmp._build_melts_input_wide_dataframe(prepared_df, params)
    assert list(wide.index[:15]) == rmp.MELTS_REQUIRED_15_ROWS
    assert "ΔT" in wide.index
    assert "ΔP" in wide.index
    assert wide.loc["Model", "S1"] == "rhyolite-MELTS_v1.0.x"
    assert float(wide.loc["T1", "S1"]) == pytest.approx(1100.0)


def test_build_melts_input_wide_dataframe_visual_print_full_submission():
    prepared_df = _minimal_prepared_df()
    params = rmp.MELTSRunParams(
        T1=1100, T2=700, dT=1, P1=400, P2=10, dP=10,
        fO2_constraint="buffered", fO2_buffer="NNO", fO2_offset=0.0
    )

    wide = rmp._build_melts_input_wide_dataframe(prepared_df, params)

    # Visual inspection aid: print the exact wide-format RMELTS batch submission matrix.
    print("\n=== RMELTS BATCH SUBMISSION (FAKE SAMPLE) : START ===")
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 200):
        print(wide.to_string())
    print("=== RMELTS BATCH SUBMISSION (FAKE SAMPLE) : END ===\n")

    assert list(wide.index[:15]) == rmp.MELTS_REQUIRED_15_ROWS
    for key in [
        "Model",
        "Calculation",
        "T1",
        "ΔT",
        "P1",
        "ΔP",
        "fO2 constraint",
        "fO2 buffer",
        "fO2 offset",
    ]:
        assert key in wide.index

    assert "S1" in wide.columns
    assert float(wide.loc["SiO2", "S1"]) == pytest.approx(75.0)
    assert float(wide.loc["H2O", "S1"]) == pytest.approx(2.0)
    assert wide.loc["Model", "S1"] == "rhyolite-MELTS_v1.0.x"
    assert wide.loc["Calculation", "S1"] == "QF_P_Calc"
    assert float(wide.loc["T1", "S1"]) == pytest.approx(1100.0)
    assert float(wide.loc["P1", "S1"]) == pytest.approx(400.0)
    assert wide.loc["fO2 constraint", "S1"] == "buffered"
    assert wide.loc["fO2 buffer", "S1"] == "NNO"
    assert float(wide.loc["fO2 offset", "S1"]) == pytest.approx(0.0)


def test_rmelts_run_import_backend_writes_manifest_and_uses_run_dir(tmp_path, monkeypatch):
    prepared_df = _minimal_prepared_df()
    prepared_path = tmp_path / "prepared.csv"
    prepared_df.to_csv(prepared_path, index=False)

    class FakeHelperModule:
        @staticmethod
        def parallel_melts_main_loop(csv_path, max_composition_workers, max_pressure_workers, verbose):
            # Should run inside the run directory due to _pushd in _run_helper_import_backend.
            cwd = Path.cwd()
            outdir = cwd / "parallel-results"
            outdir.mkdir(exist_ok=True)
            fname = outdir / "Parallel_MELTS_S1_dP10_01-01_1cores.xlsx"
            fname.write_bytes(b"fake")
            return [
                {
                    "label": "S1",
                    "filename": str(fname.relative_to(cwd)),
                    "num_pressure_steps": 3,
                    "num_data_points": 10,
                    "P_QF": 123.4,
                    "P_Q2F": 150.0,
                    "total_time": 1.5,
                    "calc_time": 1.2,
                }
            ]

    monkeypatch.setattr(rmp, "_load_helper_module", lambda helper_dir, **kwargs: FakeHelperModule)

    result = rmp.rMELTS_run(
        prepared_path,
        output_dir=tmp_path,
        dataset_name="batch",
        T1=1100,
        T2=700,
        dT=1,
        P1=400,
        P2=10,
        dP=10,
        fO2_constraint="buffered",
        fO2_buffer="NNO",
        fO2_offset=0.0,
        helper_dir=tmp_path,  # not used because loader is monkeypatched
        backend="import",
        max_composition_workers=1,
        max_pressure_workers=1,
        verbose=False,
        pressure_residual_threshold_C=10.0,
    )

    manifest = pd.read_csv(result.manifest_csv_path)
    assert manifest.loc[0, "status"] == "success"
    assert Path(manifest.loc[0, "excel_path"]).exists()
    assert Path(result.run_dir).is_dir()
    assert Path(result.melts_input_csv_path).exists()
    assert result.summary["num_success"] == 1
    for col in [
        "melts_calc_time_s",
        "pressure_calc_time_s",
        "workbook_build_time_s",
        "runtime_log_version",
        "pressure_residual_threshold_C",
    ]:
        assert col in manifest.columns
    assert manifest.loc[0, "melts_calc_time_s"] == pytest.approx(1.2)
    assert manifest.loc[0, "runtime_log_version"] == "rmp_runtime_v1"
    assert manifest.loc[0, "pressure_residual_threshold_C"] == pytest.approx(10.0)


def _write_test_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = "system"
    headers = ["Index", "T (C)", "P (MPa)", "G (kJ)", "H (kJ)", "S (J/K)", "Cp (J/K)", "V (cm3)", "mass (g)", "rho (g/cm3)", "Outcome"]
    ws0.append(headers)
    ws0.append([1, 800, 150, -1000, -900, 100, 50, 10, 100, 2.5, "Success"])

    ws1 = wb.create_sheet("liquid")
    ws1.append(headers + ["SiO2 (wt%)"])
    ws1.append([1, 800, 150, -500, -450, 80, 40, 9, 90, 2.3, "Success", 74.5])

    ws2 = wb.create_sheet("quartz")
    ws2.append(headers)
    ws2.append([1, 800, 150, -300, -280, 20, 10, 5, 10, 2.6, "Success"])

    wb.save(path)


def test_geobarometry_basis_extracts_selected_phases_and_reports_missing(tmp_path):
    wb_path = tmp_path / "sample.xlsx"
    _write_test_workbook(wb_path)

    manifest = pd.DataFrame(
        [
            {
                "dataset_name": "d",
                "sample_label": "S1",
                "status": "success",
                "excel_path": str(wb_path),
                "helper_filename": "parallel-results/sample.xlsx",
                "num_pressure_steps": 1,
                "num_data_points": 1,
                "P_QF": 100,
                "P_Q2F": 120,
                "total_time_s": 1,
                "calc_time_s": 1,
                "error": "",
                "run_dir": str(tmp_path),
                "melts_input_csv_path": str(tmp_path / "melts_input.csv"),
            }
        ]
    )
    manifest_path = tmp_path / "manifest.csv"
    manifest.to_csv(manifest_path, index=False)

    basis = rmp.rMELTS_geobarometry_basis(
        manifest_path,
        phases=["Quartz", "Kspar"],
        include_system=True,
        include_liquid=True,
        missing_phase_policy="skip_point",
    )

    assert "quartz" in basis.phase_tables
    assert "system" in basis.phase_tables
    assert "liquid" in basis.phase_tables
    assert not basis.phase_tables["quartz"].empty
    assert "G_kJ" in basis.phase_tables["quartz"].columns
    missing = basis.extraction_report["missing_phases"]
    assert any(row["phase"] == "kspar" for row in missing)
    avail = basis.phase_availability_table
    assert ((avail["requested_phase"] == "kspar") & (avail["present"] == False)).any()


def test_geobarometry_basis_fill_nan_missing_phase_policy(tmp_path):
    wb_path = tmp_path / "sample.xlsx"
    _write_test_workbook(wb_path)
    manifest_path = tmp_path / "manifest.csv"
    pd.DataFrame(
        [
            {
                "dataset_name": "d",
                "sample_label": "S1",
                "status": "success",
                "excel_path": str(wb_path),
                "helper_filename": "parallel-results/sample.xlsx",
                "num_pressure_steps": 1,
                "num_data_points": 1,
                "P_QF": 100,
                "P_Q2F": 120,
                "total_time_s": 1,
                "calc_time_s": 1,
                "error": "",
                "run_dir": str(tmp_path),
                "melts_input_csv_path": str(tmp_path / "melts_input.csv"),
            }
        ]
    ).to_csv(manifest_path, index=False)

    basis = rmp.rMELTS_geobarometry_basis(
        manifest_path,
        phases=["missing_phase"],
        include_system=False,
        include_liquid=False,
        missing_phase_policy="fill_nan",
    )
    assert "missing_phase" in basis.phase_tables
    df = basis.phase_tables["missing_phase"]
    assert len(df) == 1
    assert df.loc[0, "sample_label"] == "S1"
    assert pd.isna(df.loc[0, "T_C"])


def test_normalize_aplite_xrf_to_rowwise_mc_transposes_and_dry_normalizes(tmp_path):
    xrf = pd.DataFrame(
        [
            {"Sample Name": "SiO2", "KCP109B": 76.36, "KCP109C": 75.82},
            {"Sample Name": "TiO2", "KCP109B": 0.069, "KCP109C": 0.082},
            {"Sample Name": "Al2O3", "KCP109B": 12.65, "KCP109C": 12.95},
            {"Sample Name": "FeO*", "KCP109B": 0.52, "KCP109C": 0.66},
            {"Sample Name": "MnO", "KCP109B": 0.024, "KCP109C": 0.031},
            {"Sample Name": "MgO", "KCP109B": 0.05, "KCP109C": 0.09},
            {"Sample Name": "CaO", "KCP109B": 0.54, "KCP109C": 0.70},
            {"Sample Name": "Na2O", "KCP109B": 3.97, "KCP109C": 4.37},
            {"Sample Name": "K2O", "KCP109B": 4.61, "KCP109C": 4.01},
            {"Sample Name": "P2O5", "KCP109B": 0.006, "KCP109C": 0.009},
        ]
    )
    xrf_path = tmp_path / "Aplites_HAL_XRF_noUncertainty.csv"
    xrf.to_csv(xrf_path, index=False)

    result = rmp.normalize_aplite_xrf_to_rowwise_mc(
        xrf_path,
        output_dir=tmp_path,
        dataset_name="aplites",
        fixed_h2o_wt=13.0,
        target_samples=["KCP109B"],
    )
    all_df = pd.read_csv(result.normalized_all_rowwise_csv_path)
    one_df = pd.read_csv(result.normalized_target_rowwise_csv_path)

    assert result.num_samples == 2
    assert set(["SampleID", "FeOt", "H2O", "dry_total_original", "dry_total_normalized"]).issubset(all_df.columns)
    assert one_df["SampleID"].tolist() == ["KCP109B"]
    assert one_df.iloc[0]["H2O"] == pytest.approx(13.0)
    dry_cols = ["SiO2", "TiO2", "Al2O3", "FeOt", "MnO", "MgO", "CaO", "Na2O", "K2O", "P2O5"]
    assert one_df.iloc[0][dry_cols].sum() == pytest.approx(100.0, abs=1e-8)


def test_pressure_analysis_parser_accepts_liam_and_pipeline_labels():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pressure Analysis"
    ws.append(["Phase System", "Estimated Pressure (MPa)", "Minimum Residual (°C)", "a (quadratic)", "b (linear)", "c (constant)"])
    ws.append(["2-Phase", 325.0, 2.5, 0.01, -6.5, 1000.0])
    ws.append(["3-Phase", 300.0, 4.0, 0.02, -12.0, 1800.0])
    parsed = rmp._compute_pressure_analysis_from_workbook(wb, prefer_existing_sheet=True)
    assert parsed["P_2phase_qtz_fsp_MPa"] == pytest.approx(325.0)
    assert parsed["P_3phase_qtz_fsp_fsp1_MPa"] == pytest.approx(300.0)

    ws["A2"] = "2-Phase (Qtz+Fsp)"
    ws["A3"] = "3-Phase (Qtz+Fsp+Fsp1)"
    parsed2 = rmp._compute_pressure_analysis_from_workbook(wb, prefer_existing_sheet=True)
    assert parsed2["P_2phase_qtz_fsp_MPa"] == pytest.approx(325.0)
    assert parsed2["P_3phase_qtz_fsp_fsp1_MPa"] == pytest.approx(300.0)


def test_first_appearance_extractor_and_staged_window_selector():
    phase_tables = {
        "quartz": pd.DataFrame(
            [
                {"P_MPa": 400, "T_C": 760, "Mass_g": 0.0},
                {"P_MPa": 400, "T_C": 755, "Mass_g": 1.0},
                {"P_MPa": 375, "T_C": 750, "Mass_g": 2.0},
            ]
        ),
        "feldspar": pd.DataFrame(
            [
                {"P_MPa": 400, "T_C": 752, "Mass_g": 0.2},
                {"P_MPa": 375, "T_C": 748, "Mass_g": 0.5},
            ]
        ),
        "feldspar_1": pd.DataFrame(
            [
                {"P_MPa": 375, "T_C": 742, "Mass_g": 0.1},
                {"P_MPa": 350, "T_C": None, "Mass_g": 0.1},
            ]
        ),
    }
    first_df = rmp._extract_first_appearance_table_from_phase_tables(phase_tables)
    assert not first_df.empty
    qz_400 = first_df[(first_df["phase"] == "quartz") & (first_df["P_MPa"] == 400)]
    # zero-mass row at 760 should be ignored; first valid appearance is 755
    assert qz_400.iloc[0]["T_first_appearance_C"] == pytest.approx(755)

    window = rmp._choose_staged_temperature_window(
        first_df,
        full_T1=1100,
        full_T2=600,
        margin_high_C=30,
        margin_low_C=80,
    )
    assert window["reason"] == "from_first_appearance"
    assert window["T1_fine"] <= 1100
    assert window["T2_fine"] >= 600
    assert window["T1_fine"] > window["T2_fine"]

    fallback = rmp._choose_staged_temperature_window(
        pd.DataFrame(columns=["phase", "P_MPa", "T_first_appearance_C"]),
        full_T1=1100,
        full_T2=600,
        margin_high_C=30,
        margin_low_C=80,
    )
    assert fallback["reason"] == "no_phase_appearance_rows"
    assert fallback["T1_fine"] == pytest.approx(1100)
    assert fallback["T2_fine"] == pytest.approx(600)


def test_load_helper_module_uses_stable_name_and_sys_path(tmp_path):
    helper_path = tmp_path / "MeltsHelperFunctions.py"
    helper_path.write_text("VALUE = 1\n", encoding="utf-8")
    module = rmp._load_helper_module(tmp_path)
    assert module.__name__ == "MeltsHelperFunctions"
    assert str(tmp_path) in rmp.sys.path


def test_patch_helper_source_text_for_spawn_safety_increments_timeout_loop_counter():
    src = (
        "def f():\n"
        "    if True:\n"
        "                current_T = current_T+25  # continue with higher temperature\n"
        "                continue\n"
        "                state = equil.execute(temp + 273.15, pressure * 10, bulk_comp=blk_cmp, con_deltaNNO=fO2_offset, debug=0)\n"
        "\n"
        "                t1 = time.time()\n"
        "                calc_time += (t1 - t0)\n"
        "        temp = find_wet_liquidus(equil, T1, T2, pressure, 50, blk_cmp, fO2_offset, verbose)\n"
    )
    out = rmp._patch_helper_source_text_for_spawn_safety(src)
    assert "i = i + 1" in out
    assert "min(T1, current_T+25)" in out
    assert "safe_equilibrium_execute" in out
    assert "timeout in execute" in out
    assert "skip wet-liquidus pre-search" in out


def test_safe_float_handles_numeric_and_non_numeric_values():
    np = pytest.importorskip("numpy")
    assert rmp._safe_float(None) is None
    assert rmp._safe_float("Fit Failed") is None
    assert rmp._safe_float(1) == pytest.approx(1.0)
    assert rmp._safe_float("2.5") == pytest.approx(2.5)
    assert rmp._safe_float(float("nan")) is None
    assert rmp._safe_float(np.float64(3.2)) == pytest.approx(3.2)


def test_pressure_analysis_parser_handles_fit_failed_cells():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pressure Analysis"
    ws.append(
        [
            "Phase System",
            "Estimated Pressure (MPa)",
            "Minimum Residual (°C)",
            "a (quadratic)",
            "b (linear)",
            "c (constant)",
        ]
    )
    ws.append(["2-Phase", "Fit Failed", 8.2, None, None, None])
    ws.append(["3-Phase", 301.0, 4.1, 0.02, -12.0, 1800.0])
    parsed = rmp._read_pressure_analysis_sheet_from_workbook(wb)
    assert parsed["P_2phase_qtz_fsp_MPa"] is None
    assert parsed["Rmin_2phase_qtz_fsp_C"] == pytest.approx(8.2)
    assert parsed["P_3phase_qtz_fsp_fsp1_MPa"] == pytest.approx(301.0)


def test_pipeline_fallback_pressure_threshold_acceptance():
    wb = openpyxl.Workbook()
    ws_qz = wb.active
    ws_qz.title = "quartz"
    headers = ["Index", "T (C)", "P (MPa)", "mass (g)"]
    ws_qz.append(headers)
    ws_qz.append([1, 800, 400, 1.0])
    ws_qz.append([2, 790, 300, 1.0])
    ws_qz.append([3, 780, 200, 1.0])

    ws_f = wb.create_sheet("feldspar")
    ws_f.append(headers)
    ws_f.append([1, 788, 400, 1.0])  # |800-788| = 12
    ws_f.append([2, 783, 300, 1.0])  # |790-783| = 7
    ws_f.append([3, 768, 200, 1.0])  # |780-768| = 12

    ws_f1 = wb.create_sheet("feldspar_1")
    ws_f1.append(headers)
    ws_f1.append([1, 770, 400, 1.0])  # 3-phase spread = 30
    ws_f1.append([2, 771, 300, 1.0])  # 3-phase spread = 19
    ws_f1.append([3, 760, 200, 1.0])  # 3-phase spread = 20

    parsed5 = rmp._compute_pressure_analysis_from_workbook(wb, prefer_existing_sheet=False, residual_threshold=5.0)
    parsed10 = rmp._compute_pressure_analysis_from_workbook(wb, prefer_existing_sheet=False, residual_threshold=10.0)

    assert parsed5["P_2phase_qtz_fsp_MPa"] is None
    assert parsed10["P_2phase_qtz_fsp_MPa"] is not None
    assert parsed10["Rmin_2phase_qtz_fsp_C"] is not None
    assert parsed10["Rmin_2phase_qtz_fsp_C"] <= 10.0
    assert parsed10["P_3phase_qtz_fsp_fsp1_MPa"] is None


def test_pipeline_fallback_uses_raw_residual_threshold_before_fit(monkeypatch):
    wb = openpyxl.Workbook()
    ws_qz = wb.active
    ws_qz.title = "quartz"
    headers = ["Index", "T (C)", "P (MPa)", "mass (g)"]
    ws_qz.append(headers)
    ws_qz.append([1, 800, 400, 1.0])
    ws_qz.append([2, 790, 300, 1.0])
    ws_qz.append([3, 780, 200, 1.0])

    ws_f = wb.create_sheet("feldspar")
    ws_f.append(headers)
    ws_f.append([1, 790, 400, 1.0])  # res2 = 10
    ws_f.append([2, 780, 300, 1.0])  # res2 = 10
    ws_f.append([3, 770, 200, 1.0])  # res2 = 10

    ws_f1 = wb.create_sheet("feldspar_1")
    ws_f1.append(headers)
    ws_f1.append([1, 790, 400, 1.0])  # res3 = 10
    ws_f1.append([2, 780, 300, 1.0])  # res3 = 10
    ws_f1.append([3, 770, 200, 1.0])  # res3 = 10

    # Force fitted minima above threshold to verify acceptance still uses raw minima.
    def fake_fit(pressures, residuals):
        return (250.0, 10.8, (0.01, -1.0, 100.0))

    monkeypatch.setattr(rmp, "_fit_pressure_residual_vertex", fake_fit)
    parsed = rmp._compute_pressure_analysis_from_workbook(wb, prefer_existing_sheet=False, residual_threshold=10.0)
    assert parsed["P_2phase_qtz_fsp_MPa"] == pytest.approx(250.0)
    assert parsed["P_3phase_qtz_fsp_fsp1_MPa"] == pytest.approx(250.0)
    # Fitted residual is still reported for diagnostics even when > threshold.
    assert parsed["Rmin_3phase_qtz_fsp_fsp1_C"] == pytest.approx(10.8)


def test_create_manifest_parses_runtime_fields_with_safe_float(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_df = rmp._create_manifest_from_results(
        dataset_name="demo",
        run_dir=run_dir,
        melts_input_csv_path=run_dir / "melts_input.csv",
        expected_labels=["S1"],
        results=[
            {
                "label": "S1",
                "filename": "",
                "error": "mock error",
                "calc_time": "1.25",
                "total_time": "2.5",
                "melts_calc_time": "1.25",
                "pressure_calc_time": "0.5",
                "workbook_build_time": "0.75",
            }
        ],
        pressure_residual_threshold_C=10.0,
    )
    row = manifest_df.iloc[0]
    assert row["melts_calc_time_s"] == pytest.approx(1.25)
    assert row["pressure_calc_time_s"] == pytest.approx(0.5)
    assert row["workbook_build_time_s"] == pytest.approx(0.75)
    assert row["total_time_s"] == pytest.approx(2.5)
    assert row["pressure_residual_threshold_C"] == pytest.approx(10.0)


def test_rmelts_run_passes_pressure_threshold_to_import_backend(tmp_path, monkeypatch):
    prepared_df = _minimal_prepared_df()
    prepared_path = tmp_path / "prepared.csv"
    prepared_df.to_csv(prepared_path, index=False)

    captured: dict[str, object] = {}

    def fake_run_helper_import_backend(**kwargs):
        captured.update(kwargs)
        return [
            {
                "label": "S1",
                "filename": "",
                "error": "mock helper fail",
                "num_pressure_steps": None,
                "num_data_points": None,
                "calc_time": 1.0,
                "total_time": 1.5,
            }
        ]

    monkeypatch.setattr(rmp, "_run_helper_import_backend", fake_run_helper_import_backend)

    result = rmp.rMELTS_run(
        prepared_path,
        output_dir=tmp_path,
        dataset_name="threshold_pass",
        T1=1100,
        T2=700,
        dT=1,
        P1=400,
        P2=10,
        dP=10,
        fO2_constraint="buffered",
        fO2_buffer="NNO",
        fO2_offset=0.0,
        helper_dir=tmp_path,
        backend="import",
        max_composition_workers=1,
        max_pressure_workers=1,
        pressure_residual_threshold_C=10.0,
    )
    assert Path(result.manifest_csv_path).exists()
    assert captured["pressure_residual_threshold_C"] == pytest.approx(10.0)
