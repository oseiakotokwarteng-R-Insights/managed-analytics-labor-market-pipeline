"""Tests for the database load: no duplicates on rerun, and revisions are visible."""

import sqlite3

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
        info = dict(year=year, url="http://example", local_name="x.zip", retrieved_at="now",
                    download_status="test", size_bytes=1, sha256="abc")
        ids[year] = load.record_source_file(conn, run_id, info, 10, 10)
    return ids


def test_first_load_inserts_everything(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    counts = load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id))
    assert counts == {"inserted": len(clean_df), "changed": 0, "unchanged": 0}
    assert load.count_rows(conn) == len(clean_df)


def test_running_the_load_twice_does_not_create_duplicates(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids)
    first_hash = load.analytic_hash(conn)

    second = load.load_facts(conn, clean_df, run_id, ids)
    assert load.count_rows(conn) == len(clean_df)          # same row count
    assert second == {"inserted": 0, "changed": 0, "unchanged": len(clean_df)}
    assert load.analytic_hash(conn) == first_hash          # and identical data


def test_a_revised_value_is_counted_as_changed(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    ids = fake_sources(conn, run_id)
    load.load_facts(conn, clean_df, run_id, ids)
    before = load.analytic_hash(conn)

    revised = clean_df.copy()
    revised.loc[0, "employment"] = revised.loc[0, "employment"] + 7
    counts = load.load_facts(conn, revised, run_id, ids)

    assert counts["changed"] == 1 and counts["inserted"] == 0
    assert load.count_rows(conn) == len(clean_df)
    assert load.analytic_hash(conn) != before


def test_suppressed_values_are_stored_as_null(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    clean_df.loc[2, ["is_suppressed", "disclosure_code"]] = [1, "N"]
    clean_df.loc[2, ["establishments", "employment", "total_wages", "avg_weekly_wage",
                     "avg_annual_pay"]] = float("nan")
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id))
    stored = conn.execute("SELECT COUNT(*) FROM qcew_annual WHERE is_suppressed = 1 "
                          "AND employment IS NULL").fetchone()[0]
    assert stored == 1


def test_database_refuses_rows_with_unknown_codes(cfg, refs, clean_df):
    # Foreign keys are switched on, so a bad area code can't sneak in even if validation was skipped.
    conn, run_id = fresh_db(cfg, refs)
    clean_df.loc[0, "area_fips"] = "11777"
    try:
        load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id))
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected the database to reject the unknown area code")
    assert load.count_rows(conn) == 0     # and the failed load left nothing behind
