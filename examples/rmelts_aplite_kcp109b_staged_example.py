from __future__ import annotations

from pathlib import Path

from rmelts_mc_pipeline import (
    MC_to_csv_rMELTS,
    normalize_aplite_xrf_to_rowwise_mc,
    rMELTS_run_staged_first_appearance,
)


def main() -> None:
    """
    PyCharm-friendly staged aplite example:
    1) Normalize original XRF table to row-wise MC-style CSV
    2) Prepare one sample for MELTS input
    3) Run scout (dT=10) then narrowed fine run (dT=1)
    4) Write helper workbook(s) + pipeline summary workbook(s) with runtime accounting
    """
    project_out = Path("/Users/lopezama/PycharmProjects/rmelts_outputs_onecomp")
    xrf_csv = Path(
        "/Users/lopezama/PycharmProjects/sci-cluster/sci-data/Aplites_XRF_data/Aplites_HAL_XRF_noUncertainty.csv"
    )

    # KCP109B corresponds to the normalized XRF column naming in the source file.
    sample_label = "KCP109B"
    dataset_name = "kcp109b_aplite_norm_staged_suffixC"

    norm = normalize_aplite_xrf_to_rowwise_mc(
        xrf_csv,
        output_dir=project_out,
        dataset_name=dataset_name,
        fixed_h2o_wt=13.0,
        target_samples=[sample_label],
    )

    conversion = MC_to_csv_rMELTS(
        norm.normalized_target_rowwise_csv_path,
        output_dir=project_out,
        dataset_name=dataset_name,
        sample_id_col="SampleID",
        fe_total_col="FeOt",
        fe3fet=0.0,
        h2o_col="H2O",
        validate_only=True,
        total_tolerance_wt=20.0,
    )

    staged = rMELTS_run_staged_first_appearance(
        conversion.prepared_mc_csv_path,
        output_dir=project_out,
        dataset_name=dataset_name,
        sample_label=sample_label,
        scout_T1=1100,
        scout_T2=600,
        scout_dT=10,
        fine_dT=1,
        P1=400,
        P2=200,
        dP=25,
        fO2_constraint="TRUE",
        fO2_buffer="NNO",
        fO2_offset=0.0,
        max_composition_workers=1,
        max_pressure_workers=6,
        scout_margin_high_C=30,
        scout_margin_low_C=80,
    )

    print("Normalized all-rowwise CSV:", norm.normalized_all_rowwise_csv_path)
    print("Prepared MC CSV:", conversion.prepared_mc_csv_path)
    print("Scout manifest:", staged.scout_run_result.manifest_csv_path)
    print("Fine manifest:", staged.fine_run_result.manifest_csv_path)
    print("Scout summary workbook:", staged.scout_summary_workbook_path)
    print("Fine summary workbook:", staged.fine_summary_workbook_path)
    print("Fine window:", staged.fine_window_metadata)


if __name__ == "__main__":
    main()
