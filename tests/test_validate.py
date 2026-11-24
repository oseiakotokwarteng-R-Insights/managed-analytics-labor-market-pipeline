"""Tests for the validation step.

The pattern is the same each time: start from clean data, break it in one
specific way, and confirm the matching check notices. The first test makes
sure the clean data passes everything, otherwise the others would mean nothing.
"""

import pandas as pd

from qcew_pipeline import validate
from tests.conftest import make_row

YEARS = [2023, 2024]


def results_by_name(df, refs, cfg):
    return {r.name: r for r in validate.run_checks(df, refs, cfg, YEARS)}


def test_clean_data_passes_everything(clean_df, refs, cfg):
    results = validate.run_checks(clean_df, refs, cfg, YEARS)
    failing = [r.name for r in results if not r.passed]
    assert failing == []
    assert validate.summarize(results)["errors"] == 0


def test_duplicate_keys_are_caught(clean_df, refs, cfg):
    dup = pd.concat([clean_df, clean_df.iloc[[0]]], ignore_index=True)
    res = results_by_name(dup, refs, cfg)
    assert not res["no_duplicate_keys"].passed
    assert res["no_duplicate_keys"].level == "error"


def test_invalid_area_code_is_caught(clean_df, refs, cfg):
    clean_df.loc[0, "area_fips"] = "11777"
    assert not results_by_name(clean_df, refs, cfg)["valid_area_codes"].passed


def test_invalid_industry_code_is_caught(clean_df, refs, cfg):
    clean_df.loc[2, "industry_code"] = "99999"
    assert not results_by_name(clean_df, refs, cfg)["valid_industry_codes"].passed


def test_missing_key_field_is_caught(clean_df, refs, cfg):
    clean_df.loc[1, "industry_code"] = None
    assert not results_by_name(clean_df, refs, cfg)["required_fields_not_null"].passed


def test_suppressed_row_carrying_a_zero_is_caught(clean_df, refs, cfg):
    # This is the mistake I most want to catch: hidden value stored as 0.
    clean_df.loc[2, ["is_suppressed", "disclosure_code", "employment"]] = [1, "N", 0.0]
    assert not results_by_name(clean_df, refs, cfg)["suppressed_values_are_null_not_zero"].passed


def test_properly_suppressed_row_is_fine(clean_df, refs, cfg):
    suppressed = pd.DataFrame([make_row("11001", 2024, "74", "23", 0, suppressed=1)])
    df = pd.concat(
        [
            clean_df[~((clean_df.area_fips == "11001") & (clean_df.year == 2024) & (clean_df.industry_code == "23"))],
            suppressed,
        ],
        ignore_index=True,
    )
    res = results_by_name(df, refs, cfg)
    assert res["suppressed_values_are_null_not_zero"].passed
    assert res["suppression_rate"].failed_count == 1


def test_negative_numbers_are_caught(clean_df, refs, cfg):
    clean_df.loc[3, "employment"] = -5.0
    assert not results_by_name(clean_df, refs, cfg)["no_negative_measures"].passed


def test_a_missing_year_is_caught(clean_df, refs, cfg):
    only_2024 = clean_df[clean_df["year"] == 2024]
    assert not results_by_name(only_2024, refs, cfg)["all_years_present_with_enough_rows"].passed


def test_state_total_mismatch_is_flagged_as_warning(clean_df, refs, cfg):
    clean_df.loc[(clean_df.area_fips == "24003") & (clean_df.agglvl_code == "71"), "employment"] = 400.0
    r = results_by_name(clean_df, refs, cfg)["county_totals_agree_with_state_total"]
    assert not r.passed and r.level == "warn"


def test_sector_sum_above_county_total_is_flagged(clean_df, refs, cfg):
    clean_df.loc[(clean_df.area_fips == "24001") & (clean_df.industry_code == "23"), "employment"] = 999.0
    assert not results_by_name(clean_df, refs, cfg)["sector_sum_not_above_county_total"].passed


def test_missing_county_in_one_year_is_flagged(clean_df, refs, cfg):
    drop = (clean_df.area_fips == "24003") & (clean_df.year == 2024)
    r = results_by_name(clean_df[~drop], refs, cfg)
    assert not r["same_counties_in_every_year"].passed


def test_wage_that_disagrees_with_annual_pay_is_flagged(clean_df, refs, cfg):
    clean_df.loc[0, "avg_annual_pay"] = 99999.0
    assert not results_by_name(clean_df, refs, cfg)["annual_pay_matches_weekly_wage"].passed
