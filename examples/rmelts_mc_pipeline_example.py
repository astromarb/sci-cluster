from __future__ import annotations

from pathlib import Path

from rmelts_mc_pipeline import MC_to_csv_rMELTS, rMELTS_geobarometry_basis, rMELTS_run


def main() -> None:
    """
    PyCharm-friendly example usage for the Monte Carlo -> rhyolite-MELTS pipeline.

    Update the paths and MELTS parameters for your dataset, then run this script.
    """
    project_out = Path("/Users/lopezama/PycharmProjects/rmelts_outputs")
    mc_csv = Path("/Users/lopezama/PycharmProjects/your_monte_carlo_compositions.csv")
    helper_dir = Path("/Users/lopezama/Downloads")

    conversion = MC_to_csv_rMELTS(
        mc_csv,
        output_dir=project_out,
        dataset_name="my_aplite_mc_batch",
        sample_id_col=None,   # or e.g. "sample_id"
        fe_total_col="FeOt",
        fe3fet=0.10,
        h2o_col="H2O",
        validate_only=True,
        total_tolerance_wt=0.5,
    )

    run = rMELTS_run(
        conversion.prepared_mc_csv_path,
        output_dir=project_out,
        dataset_name="my_aplite_mc_batch",
        T1=1100,
        T2=700,
        dT=1,
        P1=400,
        P2=10,
        dP=10,
        fO2_constraint="buffered",
        fO2_buffer="NNO",
        fO2_offset=0.0,
        model="rhyolite-MELTS_v1.0.x",
        calculation="QF_P_Calc",
        max_composition_workers=2,
        max_pressure_workers=4,
        verbose=False,
        helper_dir=helper_dir,
        backend="import",   # use "cli_fallback" if direct import is unreliable in your env
    )

    basis = rMELTS_geobarometry_basis(
        run.manifest_csv_path,
        phases=["quartz", "alkali_feldspar", "plagioclase"],
        include_system=True,
        include_liquid=True,
        missing_phase_policy="skip_point",
        columns=["T_C", "P_MPa", "G_kJ", "H_kJ", "S_J_per_K", "Cp_J_per_K", "V_cm3", "Outcome"],
    )

    print("Prepared compositions:", conversion.num_samples)
    print("Manifest:", run.manifest_csv_path)
    print("Successful MELTS runs:", run.summary.get("num_success"))
    print("Extracted phase tables:", list(basis.phase_tables.keys()))


if __name__ == "__main__":
    main()

