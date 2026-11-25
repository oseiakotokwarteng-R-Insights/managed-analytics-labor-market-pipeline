"""Tests for the dbt analytics layer. They run dbt for real on a small made-up dataset, so they need DuckDB
and dbt and are skipped where those aren't installed. CI runs them."""

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from qcew_pipeline import analytics, load, publish
from tests.conftest import make_row
from tests.test_load import fake_sources, fresh_db

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("dbt.cli.main")

PROJECT = Path(__file__).resolve().parent.parent / "analytics"
HIDDEN = ["establishments", "employment", "total_wages", "avg_weekly_wage", "avg_annual_pay"]


def publish_fake_data(cfg, refs, df):
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, df, run_id, fake_sources(conn, run_id), cfg)
    conn.close()
    publish.publish_lake(cfg)


def build(cfg, **kwargs):
    return analytics.build_analytics(cfg, project_dir=PROJECT, **kwargs)


def rows(cfg, sql):
    con = duckdb.connect(str(cfg["paths"]["analytics_db"]), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def hide_one_sector_row(df):
    """Hide Anne Arundel's construction jobs in 2024, the way BLS would."""
    row = df.index[(df["area_fips"] == "24003") & (df["industry_code"] == "23") & (df["year"] == 2024)][0]
    df.loc[row, ["is_suppressed", "disclosure_code"]] = [1, "N"]
    df.loc[row, HIDDEN] = float("nan")


def test_a_clean_dataset_builds_and_every_test_passes(cfg, refs, clean_df):
    hide_one_sector_row(clean_df)
    publish_fake_data(cfg, refs, clean_df)

    assert build(cfg) is True

    # 4 counties x 2 years
    assert rows(cfg, "select count(*) from mart_county_year") == [(8,)]
    # Anne Arundel kept 200 jobs both years: no change in 2024, and nothing to compare with in 2023
    assert rows(
        cfg,
        "select year, employment, employment_change, employment_change_pct from mart_county_year "
        "where area_fips = '24003' order by year",
    ) == [(2023, 200.0, None, None), (2024, 200.0, 0.0, 0.0)]

    # the counties add up to the state totals (3 states x 2 years)
    state = rows(cfg, "select state_name, year, gap_pct from mart_state_year order by state_fips, year")
    assert [(s, y) for s, y, _ in state] == [
        ("District of Columbia", 2023), ("District of Columbia", 2024),
        ("Maryland", 2023), ("Maryland", 2024),
        ("Virginia", 2023), ("Virginia", 2024),
    ]  # fmt: skip
    assert all(gap == pytest.approx(0.0) for _, _, gap in state)

    # shares of county jobs: DC construction was 40 of 100, and the hidden row has no share, not a zero
    assert rows(
        cfg,
        "select employment_share_of_county from mart_county_sector_year "
        "where area_fips = '11001' and industry_code = '23' and year = 2024",
    ) == [(pytest.approx(0.4),)]
    assert rows(
        cfg,
        "select is_suppressed, employment, employment_share_of_county from mart_county_sector_year "
        "where area_fips = '24003' and industry_code = '23' and year = 2024",
    ) == [(True, None, None)]

    # the state names come from the seed
    assert rows(cfg, "select state_name from stg_area where area_fips = '24001'") == [("Maryland",)]


def test_a_duplicated_row_in_the_published_files_fails_the_build(cfg, refs, clean_df):
    publish_fake_data(cfg, refs, clean_df)
    part = cfg["paths"]["lake_dir"] / "qcew_annual" / "year=2024" / "data.parquet"
    rewritten = part.with_name("rewritten.parquet")
    con = duckdb.connect()
    source = part.as_posix()
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{source}') UNION ALL (SELECT * FROM read_parquet('{source}') LIMIT 1)) "
        f"TO '{rewritten.as_posix()}' (FORMAT PARQUET)"
    )
    con.close()
    rewritten.replace(part)

    assert build(cfg) is False


def test_a_hidden_value_stored_as_zero_fails_the_build(cfg, refs, clean_df):
    hide_one_sector_row(clean_df)
    publish_fake_data(cfg, refs, clean_df)
    part = cfg["paths"]["lake_dir"] / "qcew_annual" / "year=2024" / "data.parquet"
    rewritten = part.with_name("rewritten.parquet")
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * REPLACE (CASE WHEN is_suppressed = 1 THEN 0.0 ELSE employment END AS employment) "
        f"FROM read_parquet('{part.as_posix()}')) TO '{rewritten.as_posix()}' (FORMAT PARQUET)"
    )
    con.close()
    rewritten.replace(part)

    assert build(cfg) is False
    assert build(cfg, select="assert_suppressed_values_are_null") is False


def test_building_before_publishing_is_refused_with_a_clear_message(cfg, capsys, monkeypatch):
    monkeypatch.setattr(analytics, "load_config", lambda path=None, region=None: cfg)

    assert analytics.main([]) == 2

    assert "Publish first" in capsys.readouterr().err


def test_the_dbt_project_is_found_from_the_repository_folder(monkeypatch):
    monkeypatch.delenv("QCEW_ANALYTICS_DIR", raising=False)
    assert (analytics.find_project() / "dbt_project.yml").is_file()


def test_building_never_changes_this_programs_own_environment(cfg, refs, clean_df, monkeypatch):
    monkeypatch.delenv("QCEW_LAKE_DIR", raising=False)
    monkeypatch.delenv("QCEW_ANALYTICS_DB", raising=False)
    publish_fake_data(cfg, refs, clean_df)

    build(cfg, select="stg_area")

    assert "QCEW_LAKE_DIR" not in os.environ and "QCEW_ANALYTICS_DB" not in os.environ


def test_a_failed_build_leaves_the_database_file_free_to_open(cfg, refs, clean_df):
    # dbt runs in its own process, so its connection is closed the moment it finishes.
    publish_fake_data(cfg, refs, clean_df)
    assert build(cfg) is True
    assert rows(cfg, "select count(*) from stg_area")[0][0] > 0
    assert build(cfg, select="mart_county_year") is True  # and it can be built into again


def with_an_undefined_bucket(refs, df, include_bucket=True):
    """Maryland, plus a bucket of jobs BLS could not place in any county (area 24999), as in the real data.
    The state total is the counties plus the bucket."""
    df = df.copy()
    df.loc[df["area_fips"] == "24000", "employment"] = 350.0
    refs = dict(refs)
    bucket_area = pd.DataFrame({"area_fips": ["24999"], "area_title": ["Unknown Or Undefined, Maryland"]})
    refs["area"] = pd.concat([refs["area"], bucket_area], ignore_index=True)
    if include_bucket:
        bucket = pd.DataFrame([make_row("24999", year, "71", "10", 50) for year in (2023, 2024)])[df.columns]
        df = pd.concat([df, bucket], ignore_index=True)
    return refs, df


def test_jobs_that_belong_to_no_county_are_shown_separately_and_the_state_still_adds_up(cfg, refs, clean_df):
    refs2, df2 = with_an_undefined_bucket(refs, clean_df)
    publish_fake_data(cfg, refs2, df2)

    assert build(cfg) is True

    assert rows(cfg, "select count(*) from mart_county_year") == [(8,)]  # the bucket is not a county
    assert rows(
        cfg,
        "select county_sum_employment, undefined_area_employment, bls_state_employment, gap_pct, "
        "share_not_in_a_county from mart_state_year where state_name = 'Maryland' and year = 2024",
    ) == [(300.0, 50.0, 350.0, pytest.approx(0.0), pytest.approx(50 / 350))]
    assert rows(
        cfg,
        "select distinct undefined_area_employment from mart_state_year "
        "where state_name in ('Virginia', 'District of Columbia')",
    ) == [(0.0,)]


def test_a_state_total_that_the_counties_and_the_bucket_cannot_explain_fails_the_build(cfg, refs, clean_df):
    # The state says 350 jobs, but only 300 are in counties and the bucket is missing.
    refs2, df2 = with_an_undefined_bucket(refs, clean_df, include_bucket=False)
    publish_fake_data(cfg, refs2, df2)

    assert build(cfg) is False
    assert build(cfg, select="assert_county_sums_match_state_totals") is False


def test_a_database_held_open_by_another_program_gives_a_plain_message(cfg, refs, clean_df):
    publish_fake_data(cfg, refs, clean_df)
    assert build(cfg) is True  # this creates the analytics database file
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb, sys, time; con = duckdb.connect(sys.argv[1]); print('ready', flush=True); time.sleep(60)",
            str(cfg["paths"]["analytics_db"]),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        with pytest.raises(analytics.AnalyticsError, match="open in another program"):
            build(cfg)
    finally:
        holder.kill()
        holder.wait()
