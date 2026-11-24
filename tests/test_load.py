"""Tests for the database load: no duplicates on rerun, and revisions are visible."""

import sqlite3

import pandas as pd

from qcew_pipeline import load


def fresh_db(cfg, refs):
    conn = load.connect(cfg["paths"]["db_path"])
    load.init_schema(conn)
    load.load_reference_tables(conn, refs, cfg)
    run_id = load.start_run(conn, [2023, 2024])
    return conn, run_id


def fake_sources(conn, run_id):
    ids = {}
    for year in (2023, 2024):
        info = dict(
            year=year,
            url="http://example",
            local_name="x.zip",
            retrieved_at="now",
            download_status="test",
            size_bytes=1,
            sha256="abc",
        )
        ids[year] = load.record_source_file(conn, run_id, info, 10, 10)
    return ids


def test_first_load_inserts_everything(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    counts = load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    assert counts == {"inserted": len(clean_df), "changed": 0, "unchanged": 0, "removed": 0}
    assert load.count_rows(conn) == len(clean_df)


def test_running_the_load_twice_does_not_create_duplicates(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids, cfg)
    first_hash = load.analytic_hash(conn)

    second = load.load_facts(conn, clean_df, run_id, ids, cfg)
    assert load.count_rows(conn) == len(clean_df)  # same row count
    assert second == {"inserted": 0, "changed": 0, "unchanged": len(clean_df), "removed": 0}
    assert load.analytic_hash(conn) == first_hash  # and identical data


def test_a_revised_value_is_counted_as_changed(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids, cfg)
    before = load.analytic_hash(conn)

    revised = clean_df.copy()
    revised.loc[0, "employment"] = revised.loc[0, "employment"] + 7
    counts = load.load_facts(conn, revised, run_id, ids, cfg)

    assert counts["changed"] == 1 and counts["inserted"] == 0
    assert load.count_rows(conn) == len(clean_df)
    assert load.analytic_hash(conn) != before


def test_suppressed_values_are_stored_as_null(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    clean_df.loc[2, ["is_suppressed", "disclosure_code"]] = [1, "N"]
    clean_df.loc[2, ["establishments", "employment", "total_wages", "avg_weekly_wage", "avg_annual_pay"]] = float("nan")
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    stored = conn.execute("SELECT COUNT(*) FROM qcew_annual WHERE is_suppressed = 1 AND employment IS NULL").fetchone()[
        0
    ]
    assert stored == 1


def test_database_refuses_rows_with_unknown_codes(cfg, refs, clean_df):
    # Foreign keys are switched on, so a bad area code can't sneak in even if validation was skipped.
    conn, run_id = fresh_db(cfg, refs)
    clean_df.loc[0, "area_fips"] = "11777"
    try:
        load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected the database to reject the unknown area code")
    assert load.count_rows(conn) == 0  # and the failed load left nothing behind


# ---------------------------------------------------------------- rows BLS stops publishing
def test_a_row_bls_stops_publishing_is_archived_and_counted(cfg, refs, clean_df):
    cfg["load"]["max_removed_fraction"] = 0.5  # a tiny fake dataset: one row is a big share
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids, cfg)

    dropped = clean_df.iloc[[3]]  # pretend BLS no longer publishes this row
    gone = dropped.iloc[0]
    counts = load.load_facts(conn, clean_df.drop(index=3), run_id, ids, cfg)

    assert counts["removed"] == 1 and counts["inserted"] == 0
    assert load.count_rows(conn) == len(clean_df) - 1
    archived = conn.execute(
        "SELECT area_fips, year, industry_code, employment, removed_run_id FROM qcew_annual_removed"
    ).fetchall()
    assert archived == [
        (gone["area_fips"], int(gone["year"]), gone["industry_code"], float(gone["employment"]), run_id)
    ]


def test_a_row_that_comes_back_is_loaded_again_and_the_history_stays(cfg, refs, clean_df):
    cfg["load"]["max_removed_fraction"] = 0.5
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids, cfg)
    load.load_facts(conn, clean_df.drop(index=3), run_id, ids, cfg)

    counts = load.load_facts(conn, clean_df, run_id, ids, cfg)

    assert counts["inserted"] == 1 and counts["removed"] == 0
    assert load.count_rows(conn) == len(clean_df)
    assert conn.execute("SELECT COUNT(*) FROM qcew_annual_removed").fetchone()[0] == 1


def test_rows_outside_the_loaded_years_are_left_alone(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids, cfg)

    only_2024 = clean_df[clean_df["year"] == 2024]
    counts = load.load_facts(conn, only_2024, run_id, ids, cfg)

    assert counts["removed"] == 0
    assert load.count_rows(conn) == len(clean_df)  # the 2023 rows are still there


def test_narrowing_the_config_does_not_look_like_bls_removing_data(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids, cfg)

    cfg["state_fips"] = ["11", "24"]  # drop Virginia from the settings
    without_virginia = clean_df[~clean_df["area_fips"].str.startswith("51")]
    counts = load.load_facts(conn, without_virginia, run_id, ids, cfg)

    assert counts["removed"] == 0
    assert load.count_rows(conn) == len(clean_df)  # Virginia's rows were not touched


def test_losing_too_many_rows_stops_the_load_and_changes_nothing(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids, cfg)
    before = load.analytic_hash(conn)

    half = clean_df.iloc[::2]  # every other row, in both years: far above the 5% limit
    try:
        load.load_facts(conn, half, run_id, ids, cfg)
    except load.TooManyRemovedError:
        pass
    else:
        raise AssertionError("expected the load to refuse a file that drops half the rows")

    assert load.analytic_hash(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM qcew_annual_removed").fetchone()[0] == 0


def test_an_older_database_gets_the_new_column(cfg):
    conn = load.connect(cfg["paths"]["db_path"])
    conn.execute("CREATE TABLE pipeline_runs (run_id INTEGER PRIMARY KEY, started_at TEXT, status TEXT)")
    conn.commit()
    load._add_missing_columns(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_runs)")}
    assert "rows_removed" in columns


# ---------------------------------------------------------------- the staging table
def staging_tables(conn):
    return conn.execute("SELECT name FROM sqlite_temp_master WHERE type = 'table'").fetchall()


def test_the_staging_table_is_cleaned_up_after_a_load(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    assert staging_tables(conn) == []


def test_the_staging_table_is_cleaned_up_after_a_failed_load(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids, cfg)
    try:
        load.load_facts(conn, clean_df.iloc[::2], run_id, ids, cfg)  # too many rows would go
    except load.TooManyRemovedError:
        pass
    assert staging_tables(conn) == []
    assert not conn.in_transaction


def test_a_chunk_size_smaller_than_the_data_gives_the_same_result(cfg, refs, clean_df, monkeypatch):
    conn_a, run_a = fresh_db(cfg, refs)
    load.load_facts(conn_a, clean_df, run_a, fake_sources(conn_a, run_a), cfg)
    expected = load.analytic_hash(conn_a)

    monkeypatch.setattr(load, "STAGING_CHUNK", 4)  # many small pieces
    cfg["paths"]["db_path"] = cfg["paths"]["db_path"].with_name("chunked.sqlite")
    conn_b, run_b = fresh_db(cfg, refs)
    counts = load.load_facts(conn_b, clean_df, run_b, fake_sources(conn_b, run_b), cfg)

    assert counts["inserted"] == len(clean_df)
    assert load.analytic_hash(conn_b) == expected


def test_duplicate_keys_in_the_new_data_are_refused_and_nothing_is_loaded(cfg, refs, clean_df):
    # Validation normally catches duplicates first; the staging table is a second line of defence.
    conn, run_id = fresh_db(cfg, refs)
    doubled = pd.concat([clean_df, clean_df.iloc[[0]]])
    try:
        load.load_facts(conn, doubled, run_id, fake_sources(conn, run_id), cfg)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected the duplicate key to be refused")
    assert load.count_rows(conn) == 0
    assert staging_tables(conn) == []
