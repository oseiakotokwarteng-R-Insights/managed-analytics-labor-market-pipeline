"""Tests for the comparison logic in the cross-check. They need only pandas, so they run everywhere."""

import pandas as pd
import pytest

from evaluation import cross_check_analytics as cc


def test_two_missing_values_match_but_one_missing_value_is_a_difference():
    left = pd.DataFrame({"k": [1, 2, 3], "v": [1.0, None, 3.0]})
    right = pd.DataFrame({"k": [1, 2, 3], "v": [1.0, None, None]})

    result = cc.compare("t", left, right, ["k"])

    assert result["problems"] == 1 and not result["passed"]
    assert "k=3" in result["examples"][0]


def test_rows_found_on_only_one_side_are_reported():
    left = pd.DataFrame({"k": [1, 2], "v": [1.0, 2.0]})
    right = pd.DataFrame({"k": [2, 3], "v": [2.0, 3.0]})

    result = cc.compare("t", left, right, ["k"])

    assert result["problems"] == 2 and result["rows_compared"] == 1
    assert "only in the database: k=1" in result["examples"] and "only in dbt: k=3" in result["examples"]


def test_matching_tables_pass_and_comparing_nothing_does_not():
    table = pd.DataFrame({"k": [1, 2], "v": [1.5, 2.5], "name": ["a", "b"]})
    assert cc.compare("t", table, table.copy(), ["k"])["passed"]
    empty = table.iloc[0:0]
    assert not cc.compare("t", empty, empty, ["k"])["passed"]


def test_tiny_floating_point_noise_is_not_a_difference():
    left = pd.DataFrame({"k": [1], "v": [0.1 + 0.2]})
    right = pd.DataFrame({"k": [1], "v": [0.3]})
    assert cc.compare("t", left, right, ["k"])["passed"]


def test_year_over_year_only_compares_with_the_previous_calendar_year_and_skips_hidden_values():
    county = pd.DataFrame(
        {
            "area_fips": ["A"] * 4 + ["B"] * 2,
            "year": [2021, 2022, 2024, 2025, 2021, 2022],
            "employment": [100.0, 110.0, 120.0, None, 50.0, None],
            "avg_weekly_wage": [1000.0, 1100.0, 1100.0, 1200.0, 900.0, 950.0],
        }
    )

    out = cc.year_over_year(county).set_index(["area_fips", "year"])

    assert pd.isna(out.loc[("A", 2021), "employment_change"])  # nothing before 2021
    assert out.loc[("A", 2022), "employment_change_pct"] == pytest.approx(0.1)
    assert pd.isna(out.loc[("A", 2024), "employment_change"])  # 2023 is missing, so no comparison
    assert pd.isna(out.loc[("A", 2025), "employment_change"])  # 2025 is hidden
    assert out.loc[("A", 2025), "avg_weekly_wage_change_pct"] == pytest.approx(1200 / 1100 - 1)
    assert pd.isna(out.loc[("B", 2022), "employment_change"])  # hidden value, so no change
    assert out.loc[("B", 2022), "avg_weekly_wage_change_pct"] == pytest.approx(950 / 900 - 1)
