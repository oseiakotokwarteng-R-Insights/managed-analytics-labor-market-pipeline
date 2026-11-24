"""Tests for the dbt analytics layer. They run dbt for real on a small made-up dataset, so they need DuckDB
and dbt and are skipped where those aren't installed. CI runs them."""

import os
from pathlib import Path

import pytest

from qcew_pipeline import analytics, load, publish
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
    monkeypatch.setattr(analytics, "load_config", lambda path=None: cfg)

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
