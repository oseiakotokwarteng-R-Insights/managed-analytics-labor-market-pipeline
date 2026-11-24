"""Tests for publishing the data as Parquet files. They need DuckDB, so they are skipped without it."""

import json

import pytest

from qcew_pipeline import load, publish
from tests.test_load import fake_sources, fresh_db

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def loaded(cfg, refs, clean_df, tmp_path):
    """A database with the fake data loaded, and a folder to publish into."""
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    conn.close()
    return tmp_path / "lake"


def query(lake, sql):
    return duckdb.connect().execute(sql.replace("LAKE", lake.as_posix())).fetchall()


FACTS = "read_parquet('LAKE/qcew_annual/*/data.parquet', hive_partitioning = true)"


def test_one_folder_per_year_plus_the_code_lists_and_a_manifest(cfg, loaded):
    publish.publish_lake(cfg, loaded)

    assert (loaded / "qcew_annual" / "year=2023" / "data.parquet").is_file()
    assert (loaded / "qcew_annual" / "year=2024" / "data.parquet").is_file()
    for name in ("dim_area", "dim_industry", "dim_ownership", "dim_agglevel"):
        assert (loaded / f"{name}.parquet").is_file()
    assert (loaded / "manifest.json").is_file()


def test_the_files_hold_the_same_rows_as_the_database(cfg, loaded, clean_df):
    publish.publish_lake(cfg, loaded)

    (count,) = query(loaded, f"SELECT COUNT(*) FROM {FACTS}")[0]
    assert count == len(clean_df)
    by_year = dict(query(loaded, f"SELECT year, COUNT(*) FROM {FACTS} GROUP BY year"))
    assert by_year == {2023: len(clean_df) // 2, 2024: len(clean_df) // 2}
    (total,) = query(loaded, f"SELECT SUM(employment) FROM {FACTS}")[0]
    assert total == clean_df["employment"].sum()


def test_hidden_values_stay_empty_and_are_never_turned_into_zeros(cfg, refs, clean_df, tmp_path):
    hidden = clean_df.index[(clean_df["area_fips"] == "24001") & (clean_df["agglvl_code"] == "71")][0]
    clean_df.loc[hidden, ["is_suppressed", "disclosure_code"]] = [1, "N"]
    clean_df.loc[hidden, ["establishments", "employment", "total_wages", "avg_weekly_wage", "avg_annual_pay"]] = (
        float("nan")
    )
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    conn.close()
    lake = tmp_path / "lake"

    publish.publish_lake(cfg, lake)

    rows = query(lake, f"SELECT employment, avg_weekly_wage FROM {FACTS} WHERE is_suppressed = 1")
    assert rows == [(None, None)]
    (zeros,) = query(lake, f"SELECT COUNT(*) FROM {FACTS} WHERE is_suppressed = 1 AND employment = 0")[0]
    assert zeros == 0


def test_each_row_knows_which_downloaded_file_it_came_from(cfg, loaded):
    publish.publish_lake(cfg, loaded)
    assert query(loaded, f"SELECT DISTINCT source_sha256 FROM {FACTS}") == [("abc",)]     # fake_sources uses "abc"


def test_the_code_lists_carry_the_names(cfg, loaded):
    publish.publish_lake(cfg, loaded)
    rows = query(loaded, "SELECT area_title, area_type FROM read_parquet('LAKE/dim_area.parquet') "
                         "WHERE area_fips = '24001'")
    assert rows == [("Allegany", "county")]


def test_the_manifest_matches_the_database(cfg, loaded, clean_df):
    manifest = publish.publish_lake(cfg, loaded)

    saved = json.loads((loaded / "manifest.json").read_text(encoding="utf-8"))
    assert saved == manifest
    assert manifest["tables"]["qcew_annual"]["rows"] == len(clean_df)
    assert sorted(manifest["tables"]["qcew_annual"]["partitions"]) == ["2023", "2024"]
    conn = load.connect(cfg["paths"]["db_path"])
    try:
        assert manifest["analytic_hash"] == load.analytic_hash(conn)
    finally:
        conn.close()
    assert str(cfg["paths"]["db_path"]) not in json.dumps(manifest)       # no folder names from this computer


def test_publishing_twice_gives_the_same_data_and_leaves_nothing_behind(cfg, loaded):
    publish.publish_lake(cfg, loaded)
    first = query(loaded, f"SELECT * FROM {FACTS} ORDER BY year, area_fips, industry_code")

    publish.publish_lake(cfg, loaded)

    assert query(loaded, f"SELECT * FROM {FACTS} ORDER BY year, area_fips, industry_code") == first
    assert sorted(p.name for p in loaded.parent.iterdir()) == ["lake"]         # no .new or .old folders


def test_an_interrupted_earlier_attempt_does_not_get_in_the_way(cfg, loaded):
    leftover = loaded.with_name("lake.new")
    leftover.mkdir(parents=True)
    (leftover / "junk.txt").write_text("left over from a crash", encoding="utf-8")

    publish.publish_lake(cfg, loaded)

    assert not leftover.exists() and (loaded / "manifest.json").is_file()


def test_an_empty_database_is_refused_and_nothing_is_written(cfg, refs, tmp_path):
    conn, _ = fresh_db(cfg, refs)
    conn.close()
    lake = tmp_path / "lake"

    with pytest.raises(ValueError, match="no data to publish"):
        publish.publish_lake(cfg, lake)

    assert not lake.exists()


def test_the_command_line_publishes_to_the_folder_it_is_given(cfg, loaded, monkeypatch, capsys):
    monkeypatch.setattr(publish, "load_config", lambda path=None: cfg)

    assert publish.main(["--out", str(loaded)]) == 0

    assert "Published" in capsys.readouterr().out and (loaded / "manifest.json").is_file()
