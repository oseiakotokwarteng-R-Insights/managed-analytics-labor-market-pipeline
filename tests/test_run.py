"""Tests for the whole pipeline run, using fake data so nothing is downloaded from BLS.

The download and file-reading steps are swapped for versions that hand back the small
made-up dataset from conftest.py. Everything after that (validate, load, bookkeeping)
is the real code.
"""

import sqlite3

import pytest

from qcew_pipeline import collect, load, run, transform


@pytest.fixture
def pipeline(monkeypatch, cfg, refs, clean_df):
    """Returns a function that runs the pipeline over the given fake data."""
    def go(data):
        frames = {int(y): g.reset_index(drop=True) for y, g in data.groupby("year")}
        monkeypatch.setattr(collect, "fetch_reference_tables", lambda c: refs)
        monkeypatch.setattr(collect, "download_annual_file", lambda y, c: dict(
            year=y, url="http://example", local_path=str(y), local_name=f"{y}.zip", retrieved_at="now",
            download_status="test", size_bytes=1, sha256="abc"))
        monkeypatch.setattr(transform, "read_year",
                            lambda path, c: (frames[int(path)], {"rows_scanned": 99, "rows_kept": len(frames[int(path)])}))
        monkeypatch.setattr(transform, "standardize", lambda raw: raw)
        return run.run_pipeline(cfg, years=sorted(frames))
    return go


def last_run(cfg):
    conn = sqlite3.connect(cfg["paths"]["db_path"])
    row = conn.execute("SELECT status, rows_inserted, rows_removed FROM pipeline_runs ORDER BY run_id DESC").fetchone()
    conn.close()
    return row


def test_a_clean_run_succeeds_and_a_second_run_changes_nothing(pipeline, cfg, clean_df):
    first = pipeline(clean_df)
    second = pipeline(clean_df)

    assert first["status"] == "success" and first["load"]["inserted"] == len(clean_df)
    assert second["status"] == "success"
    assert second["load"] == {"inserted": 0, "changed": 0, "unchanged": len(clean_df), "removed": 0}
    assert second["analytic_hash"] == first["analytic_hash"]


def test_a_row_that_disappears_is_recorded_on_the_run(pipeline, cfg, clean_df):
    pipeline(clean_df)
    gone = clean_df[(clean_df["area_fips"] == "24003") & (clean_df["year"] == 2024)
                    & (clean_df["industry_code"] == "23")].index
    assert len(gone) == 1

    result = pipeline(clean_df.drop(index=gone))

    assert result["status"] == "success" and result["load"]["removed"] == 1
    assert last_run(cfg) == ("success", 0, 1)


def test_a_file_that_drops_most_sector_rows_is_stopped_before_anything_changes(pipeline, cfg, clean_df):
    first = pipeline(clean_df)
    no_sectors = clean_df[clean_df["agglvl_code"] != "74"]          # about 40% of the rows vanish

    result = pipeline(no_sectors)

    assert result["status"] == "failed_validation"
    assert "Nothing was changed" in result["error"]
    conn = load.connect(cfg["paths"]["db_path"])
    assert load.analytic_hash(conn) == first["analytic_hash"]
    assert conn.execute("SELECT COUNT(*) FROM qcew_annual_removed").fetchone()[0] == 0
    conn.close()
    assert last_run(cfg)[0] == "failed_validation"
