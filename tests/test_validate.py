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


# ---------------------------------------------------------------- drift warnings
def drift(df, refs, cfg, scanned=None):
    return {r.name: r for r in validate.run_checks(df, refs, cfg, YEARS, scanned_by_year=scanned)}


def hide(df, rows):
    """Mark the given rows as suppressed, the way transform.standardize does."""
    df.loc[rows, ["is_suppressed", "disclosure_code"]] = [1, "N"]
    df.loc[rows, ["establishments", "employment", "total_wages", "avg_weekly_wage", "avg_annual_pay"]] = float("nan")


def test_stable_years_pass_both_drift_checks(clean_df, refs, cfg):
    res = drift(clean_df, refs, cfg, {2023: 1000, 2024: 1010})
    assert res["suppression_rate_stable"].passed and res["source_rows_stable"].passed


def test_a_jump_in_hidden_values_is_a_warning_and_never_an_error(clean_df, refs, cfg):
    hide(clean_df, clean_df.index[clean_df["year"] == 2024][:4])      # 4 of 13 rows: a jump of about 31 points
    results = validate.run_checks(clean_df, refs, cfg, YEARS, scanned_by_year={2023: 1000, 2024: 1000})
    found = {r.name: r for r in results}["suppression_rate_stable"]

    assert not found.passed and found.level == "warn"
    assert "2024" in found.detail and "from 2023" in found.detail
    assert validate.summarize(results)["errors"] == 0                 # drift never blocks a load


def test_a_big_drop_in_rows_from_bls_is_a_warning(clean_df, refs, cfg):
    found = drift(clean_df, refs, cfg, {2023: 1000, 2024: 800})["source_rows_stable"]
    assert not found.passed and found.level == "warn"
    assert "2024" in found.detail and "20.0% fewer than 2023" in found.detail


def test_a_big_increase_in_rows_is_flagged_too(clean_df, refs, cfg):
    found = drift(clean_df, refs, cfg, {2023: 1000, 2024: 1300})["source_rows_stable"]
    assert not found.passed and "30.0% more than 2023" in found.detail


def test_a_change_just_under_the_limit_is_fine(clean_df, refs, cfg):
    assert drift(clean_df, refs, cfg, {2023: 1000, 2024: 1099})["source_rows_stable"].passed


def test_the_limits_come_from_the_settings(clean_df, refs, cfg):
    cfg["validation"]["source_rows_change_fraction"] = 0.5
    cfg["validation"]["suppression_jump_points"] = 50
    hide(clean_df, clean_df.index[clean_df["year"] == 2024][:4])
    res = drift(clean_df, refs, cfg, {2023: 1000, 2024: 800})
    assert res["source_rows_stable"].passed and res["suppression_rate_stable"].passed


def test_without_two_years_to_compare_the_checks_pass_and_say_why(clean_df, refs, cfg):
    one_year = clean_df[clean_df["year"] == 2024]
    res = {r.name: r for r in validate.run_checks(one_year, refs, cfg, [2024], scanned_by_year={2024: 1000})}
    assert res["suppression_rate_stable"].passed and "at least two years" in res["suppression_rate_stable"].detail
    assert res["source_rows_stable"].passed and "at least two years" in res["source_rows_stable"].detail


def test_the_row_count_check_says_so_when_it_was_given_no_counts(clean_df, refs, cfg):
    found = drift(clean_df, refs, cfg, scanned=None)["source_rows_stable"]
    assert found.passed and "needs row counts" in found.detail


def test_the_pattern_seen_in_the_real_2021_to_2025_run_flags_only_2025():
    # Shares of hidden values from the real run: 24, 24, 25, 28, then 36 percent (scaled to 100 rows a year).
    hidden = {2021: 24, 2022: 24, 2023: 25, 2024: 28, 2025: 36}
    rows = [
        make_row("11001", year, "74", str(i), 10, suppressed=int(i < k))
        for year, k in hidden.items()
        for i in range(100)
    ]
    found = validate._suppression_drift(pd.DataFrame(rows), 5.0)
    assert not found.passed and found.failed_count == 1 and found.detail.startswith("2025: 36.0% hidden, up 8.0")

    # National row counts from the same run.
    scanned = {2021: 3_631_807, 2022: 3_619_437, 2023: 3_650_651, 2024: 3_664_909, 2025: 3_089_813}
    volume = validate._source_volume_drift(scanned, 0.10)
    assert not volume.passed and volume.failed_count == 1 and "15.7% fewer than 2024" in volume.detail
