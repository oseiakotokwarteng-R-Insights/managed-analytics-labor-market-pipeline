"""Tests for the cross-check between the database, pandas and the dbt tables. They run dbt for real on a small
made-up dataset, so they are skipped where DuckDB and dbt aren't installed. CI runs them."""

import pytest

from evaluation import cross_check_analytics as cc
from qcew_pipeline import analytics, load, publish
from tests.test_analytics import PROJECT, hide_one_sector_row, publish_fake_data, with_an_undefined_bucket
from tests.test_load import fake_sources, fresh_db

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("dbt.cli.main")


@pytest.fixture
def built(cfg, refs, clean_df, monkeypatch):
    """A database, its published files, and the dbt tables built from them."""
    hide_one_sector_row(clean_df)
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    conn.close()
    publish.publish_lake(cfg)
    assert analytics.build_analytics(cfg, project_dir=PROJECT) is True
    monkeypatch.setattr(cc, "load_config", lambda path=None, region=None: cfg)
    return cfg


def test_everything_matches_on_a_clean_build(built, capsys):
    assert cc.main([]) == 0

    out = capsys.readouterr().out
    assert "Everything matches" in out and "FAIL" not in out
    assert (built["paths"]["output_dir"] / "cross_check_analytics.md").is_file()


def test_every_check_compared_some_rows(built):
    results = cc.run_checks(built)
    assert len(results) == 6
    assert all(r["passed"] and r["rows_compared"] > 0 for r in results)


def test_a_number_changed_in_the_database_after_publishing_is_caught(built, capsys):
    conn = load.connect(built["paths"]["db_path"])
    conn.execute(
        "UPDATE qcew_annual SET employment = employment + 1 WHERE area_fips = '24003' AND year = 2024 "
        "AND agglvl_code = '71'"
    )
    conn.commit()
    conn.close()

    assert cc.main([]) == 1

    out = capsys.readouterr().out
    assert "FAIL" in out and "the database has changed since the files were published" in out


def test_a_number_changed_in_a_dbt_table_is_caught_and_named(built, capsys):
    con = duckdb.connect(str(built["paths"]["analytics_db"]))
    con.execute("UPDATE mart_county_year SET employment = employment + 5 WHERE area_fips = '24003' AND year = 2024")
    con.close()

    assert cc.main([]) == 1

    out = capsys.readouterr().out
    assert "area_fips=24003" in out and "employment is 200.0 in the database but 205.0 in dbt" in out


def test_a_missing_row_in_a_dbt_table_is_caught(built, capsys):
    con = duckdb.connect(str(built["paths"]["analytics_db"]))
    con.execute("DELETE FROM mart_county_sector_year WHERE area_fips = '11001' AND industry_code = '23'")
    con.close()

    assert cc.main([]) == 1

    assert "only in the database" in capsys.readouterr().out


def test_running_before_building_gives_a_clear_message(cfg, capsys, monkeypatch):
    monkeypatch.setattr(cc, "load_config", lambda path=None, region=None: cfg)

    assert cc.main([]) == 2

    assert "not found" in capsys.readouterr().err


def test_the_state_check_counts_the_jobs_that_belong_to_no_county(cfg, refs, clean_df, monkeypatch, capsys):
    refs2, df2 = with_an_undefined_bucket(refs, clean_df)
    publish_fake_data(cfg, refs2, df2)
    assert analytics.build_analytics(cfg, project_dir=PROJECT) is True
    monkeypatch.setattr(cc, "load_config", lambda path=None, region=None: cfg)

    assert cc.main([]) == 0

    assert "Everything matches" in capsys.readouterr().out
