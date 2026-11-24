"""Tests for reading one year's zip file: the part of the pipeline that touches the raw BLS layout."""

import zipfile

import pandas as pd
import pytest

from qcew_pipeline import transform

COLUMNS = transform.REQUIRED_RAW_COLUMNS


def raw_row(area, own="5", agglvl="71", size="0", industry="10", disclosure="", employment="100"):
    return {
        "area_fips": area,
        "own_code": own,
        "industry_code": industry,
        "agglvl_code": agglvl,
        "size_code": size,
        "year": "2024",
        "qtr": "A",
        "disclosure_code": disclosure,
        "annual_avg_estabs": "10",
        "annual_avg_emplvl": employment,
        "total_annual_wages": "5000",
        "annual_avg_wkly_wage": "1000",
        "avg_annual_pay": "52000",
    }


def make_zip(tmp_path, rows, extra_columns=(), name="2024.zip", csv_names=("2024.annual.singlefile.csv",)):
    path = tmp_path / name
    df = pd.DataFrame(rows)
    for col in extra_columns:
        df[col] = "x"
    with zipfile.ZipFile(path, "w") as z:
        for csv_name in csv_names:
            z.writestr(csv_name, df.to_csv(index=False))
    return path


def test_only_the_rows_the_config_asks_for_are_kept(cfg, tmp_path):
    rows = [
        raw_row("11001"),  # DC county total, private: keep
        raw_row("24000", agglvl="51"),  # Maryland state total: keep
        raw_row("51013", agglvl="74", industry="23"),  # Arlington sector row: keep
        raw_row("11001", own="0"),  # all ownerships, not private: drop
        raw_row("11001", size="1"),  # one size class, not "all sizes": drop
        raw_row("48201"),  # Texas: drop
        raw_row("C1234"),  # a metro area code, not 5 digits: drop
        raw_row("11001", agglvl="78"),  # a level the config doesn't list: drop
    ]
    zip_path = make_zip(tmp_path, rows)

    kept, stats = transform.read_year(zip_path, cfg)

    assert stats == {"rows_scanned": 8, "rows_kept": 3}
    assert sorted(kept["area_fips"]) == ["11001", "24000", "51013"]


def test_codes_stay_as_text_so_leading_zeros_survive(cfg, tmp_path):
    cfg["state_fips"] = ["01"]
    kept, _ = transform.read_year(make_zip(tmp_path, [raw_row("01001")]), cfg)
    assert kept["area_fips"].tolist() == ["01001"]


def test_a_file_with_no_matching_rows_gives_an_empty_table_not_an_error(cfg, tmp_path):
    kept, stats = transform.read_year(make_zip(tmp_path, [raw_row("48201")]), cfg)
    assert stats == {"rows_scanned": 1, "rows_kept": 0}
    assert kept.empty and list(kept.columns) == COLUMNS


def test_extra_columns_in_the_file_are_ignored(cfg, tmp_path):
    kept, _ = transform.read_year(make_zip(tmp_path, [raw_row("11001")], extra_columns=["lq_disclosure_code"]), cfg)
    assert list(kept.columns) == COLUMNS


def test_a_changed_file_layout_fails_loudly_instead_of_producing_garbage(cfg, tmp_path):
    row = {k: v for k, v in raw_row("11001").items() if k != "annual_avg_emplvl"}
    with pytest.raises(ValueError, match="missing expected columns"):
        transform.read_year(make_zip(tmp_path, [row]), cfg)


def test_a_zip_without_exactly_one_csv_is_refused(cfg, tmp_path):
    two = make_zip(tmp_path, [raw_row("11001")], csv_names=("a.csv", "b.csv"), name="two.zip")
    none = make_zip(tmp_path, [raw_row("11001")], csv_names=("notes.txt",), name="none.zip")
    for path in (two, none):
        with pytest.raises(ValueError, match="Expected one CSV"):
            transform.read_year(path, cfg)


def test_a_file_that_is_not_a_zip_is_refused(cfg, tmp_path):
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"this is not a zip file")
    with pytest.raises(zipfile.BadZipFile):
        transform.read_year(bad, cfg)


def test_suppressed_rows_survive_reading_and_standardizing_as_blank_not_zero(cfg, tmp_path):
    path = make_zip(tmp_path, [raw_row("11001", disclosure="N", employment="0")])
    kept, _ = transform.read_year(path, cfg)
    clean = transform.standardize(kept)
    assert clean.loc[0, "is_suppressed"] == 1 and pd.isna(clean.loc[0, "employment"])
