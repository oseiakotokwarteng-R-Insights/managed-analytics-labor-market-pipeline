"""Tests for the CSV exports."""

import pandas as pd

from qcew_pipeline import export, load
from tests.test_load import fake_sources, fresh_db


def loaded_db(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    conn.close()


def test_both_csv_files_are_written_with_the_expected_rows(cfg, refs, clean_df, tmp_path):
    loaded_db(cfg, refs, clean_df)

    written = export.export_all(cfg, out_dir=tmp_path / "csv")

    assert [p.name for p in written] == ["county_private_totals.csv", "county_private_sectors.csv"]
    totals = pd.read_csv(written[0], dtype={"area_fips": str})
    sectors = pd.read_csv(written[1], dtype={"area_fips": str})
    assert len(totals) == 8 and len(sectors) == 12  # 4 counties x 2 years; 6 sector rows x 2 years
    assert set(totals["area_fips"]) == {"11001", "24001", "24003", "51013"}  # counties only, no state rows
    assert {"area_title", "is_suppressed", "avg_weekly_wage"} <= set(totals.columns)


def test_a_hidden_value_is_exported_as_a_blank_cell_with_its_flag(cfg, refs, clean_df, tmp_path):
    suppressed = clean_df.index[
        (clean_df["area_fips"] == "24001") & (clean_df["agglvl_code"] == "71") & (clean_df["year"] == 2024)
    ][0]
    clean_df.loc[suppressed, ["is_suppressed", "disclosure_code"]] = [1, "N"]
    clean_df.loc[suppressed, ["establishments", "employment", "total_wages", "avg_weekly_wage", "avg_annual_pay"]] = (
        float("nan")
    )
    loaded_db(cfg, refs, clean_df)

    path = export.export_all(cfg, out_dir=tmp_path / "csv")[0]
    row = pd.read_csv(path, dtype={"area_fips": str}).query("area_fips == '24001' and year == 2024").iloc[0]

    assert row["is_suppressed"] == 1 and pd.isna(row["employment"])


def test_the_default_folder_is_inside_the_output_folder(cfg, refs, clean_df):
    loaded_db(cfg, refs, clean_df)
    written = export.export_all(cfg)
    assert all(p.parent == cfg["paths"]["output_dir"] / "exports" for p in written)
