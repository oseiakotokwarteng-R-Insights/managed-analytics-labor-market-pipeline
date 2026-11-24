"""Tests for the transform step: filtering and the suppression rule."""

import zipfile

import pandas as pd

from qcew_pipeline import transform

RAW_COLUMNS = transform.REQUIRED_RAW_COLUMNS


def raw_row(**overrides):
    row = dict(
        area_fips="11001",
        own_code="5",
        industry_code="10",
        agglvl_code="71",
        size_code="0",
        year="2024",
        qtr="A",
        disclosure_code=None,
        annual_avg_estabs="100",
        annual_avg_emplvl="5000",
        total_annual_wages="500000000",
        annual_avg_wkly_wage="1900",
        avg_annual_pay="98800",
    )
    row.update(overrides)
    return row


def make_zip(tmp_path, rows, columns=None):
    df = pd.DataFrame(rows, columns=columns or RAW_COLUMNS)
    path = tmp_path / "2024_annual_singlefile.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("2024.annual.singlefile.csv", df.to_csv(index=False))
    return path


def test_filter_keeps_only_dmv_private_county_and_state_rows(cfg, tmp_path):
    rows = [
        raw_row(),  # keep: DC county total
        raw_row(area_fips="24003"),  # keep: Maryland
        raw_row(area_fips="06037"),  # drop: California
        raw_row(area_fips="C1234"),  # drop: not a 5-digit code
        raw_row(own_code="0"),  # drop: not private
        raw_row(agglvl_code="75"),  # drop: 3-digit NAICS, not in my config
        raw_row(size_code="3"),  # drop: not all sizes
        raw_row(area_fips="51000", agglvl_code="51"),  # keep: Virginia state total
    ]
    raw, stats = transform.read_year(make_zip(tmp_path, rows), cfg)
    assert stats["rows_scanned"] == 8
    assert sorted(raw["area_fips"]) == ["11001", "24003", "51000"]


def test_missing_expected_column_fails_loudly(cfg, tmp_path):
    cols = [c for c in RAW_COLUMNS if c != "annual_avg_emplvl"]
    path = make_zip(tmp_path, [raw_row()], columns=cols + [])
    try:
        transform.read_year(path, cfg)
    except ValueError as err:
        assert "annual_avg_emplvl" in str(err)
    else:
        raise AssertionError("expected a ValueError for the missing column")


def test_suppressed_values_become_null_not_zero():
    raw = pd.DataFrame(
        [
            raw_row(disclosure_code="N", annual_avg_emplvl="0", annual_avg_wkly_wage="0"),
            raw_row(area_fips="24003", annual_avg_emplvl="0"),  # a real zero: not suppressed
        ]
    )
    df = transform.standardize(raw)
    suppressed, real_zero = df.iloc[0], df.iloc[1]
    assert suppressed["is_suppressed"] == 1
    assert pd.isna(suppressed["employment"]) and pd.isna(suppressed["avg_weekly_wage"])
    assert real_zero["is_suppressed"] == 0
    assert real_zero["employment"] == 0  # genuine zeros stay zero


def test_columns_are_renamed_and_typed():
    df = transform.standardize(pd.DataFrame([raw_row()]))
    assert list(df.columns) == transform.OUTPUT_COLUMNS
    assert df.loc[0, "employment"] == 5000
    assert int(df.loc[0, "year"]) == 2024


def test_area_type_labels():
    assert transform.area_type("11000") == "state"
    assert transform.area_type("24999") == "undefined"
    assert transform.area_type("24031") == "county"
