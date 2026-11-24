"""Step 4: put validated data into SQLite.

The big idea here is that running the pipeline twice must not create
duplicates. Every row has a primary key (area + year + ownership + industry),
and loading is an "upsert": insert if new, update if it already exists.
I also count how many existing rows actually changed, so if BLS revises
history I can see that instead of it happening silently.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .transform import MEASURES, area_type

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()


# ---------------------------------------------------------------- run bookkeeping
def start_run(conn, years):
    cur = conn.execute(
        "INSERT INTO pipeline_runs (started_at, status, years) VALUES (?, 'running', ?)",
        (now_iso(), json.dumps(list(years))))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id, status, **fields):
    """Close out a run. Any extra keyword args must be column names of pipeline_runs."""
    allowed = {"rows_scanned", "rows_kept", "rows_inserted", "rows_changed", "rows_unchanged",
               "validation_errors", "validation_warnings", "analytic_hash", "notes"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Unknown run fields: {bad}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    sql = f"UPDATE pipeline_runs SET finished_at = ?, status = ?{', ' + sets if sets else ''} WHERE run_id = ?"
    conn.execute(sql, [now_iso(), status, *fields.values(), run_id])
    conn.commit()


def record_source_file(conn, run_id, info, rows_scanned, rows_kept):
    cur = conn.execute(
        """INSERT INTO source_files (run_id, year, url, local_name, retrieved_at, download_status,
                                     size_bytes, sha256, rows_scanned, rows_kept)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (run_id, info["year"], info["url"], info["local_name"], info["retrieved_at"],
         info["download_status"], info["size_bytes"], info["sha256"], rows_scanned, rows_kept))
    conn.commit()
    return cur.lastrowid


def record_validation(conn, run_id, results):
    conn.executemany(
        """INSERT INTO validation_results (run_id, check_name, level, passed, failed_count, detail)
           VALUES (?,?,?,?,?,?)""",
        [(run_id, r.name, r.level, int(r.passed), r.failed_count, r.detail) for r in results])
    conn.commit()


# ---------------------------------------------------------------- reference tables
def load_reference_tables(conn, refs, cfg):
    """Refresh the lookup tables from the BLS code files."""
    area = refs["area"].copy()
    area = area[area["area_fips"].str[:2].isin(cfg["state_fips"]) & area["area_fips"].str.fullmatch(r"\d{5}")]
    area["state_fips"] = area["area_fips"].str[:2]
    area["area_type"] = area["area_fips"].map(area_type)
    conn.executemany(
        "INSERT OR REPLACE INTO dim_area (area_fips, area_title, state_fips, area_type) VALUES (?,?,?,?)",
        area[["area_fips", "area_title", "state_fips", "area_type"]].itertuples(index=False, name=None))

    conn.executemany("INSERT OR REPLACE INTO dim_industry VALUES (?,?)",
                     refs["industry"][["industry_code", "industry_title"]].itertuples(index=False, name=None))
    conn.executemany("INSERT OR REPLACE INTO dim_ownership VALUES (?,?)",
                     refs["ownership"][["own_code", "own_title"]].itertuples(index=False, name=None))
    conn.executemany("INSERT OR REPLACE INTO dim_agglevel VALUES (?,?)",
                     refs["agglevel"][["agglvl_code", "agglvl_title"]].itertuples(index=False, name=None))
    conn.commit()


# ---------------------------------------------------------------- the main load
KEY = ["area_fips", "year", "own_code", "industry_code"]


def _as_none(value):
    """sqlite3 wants None, not pandas NA/NaN, for NULL."""
    return None if pd.isna(value) else value


def load_facts(conn, df, run_id, source_ids_by_year):
    """Upsert the standardized rows. Returns counts of inserted / changed / unchanged rows.

    Everything happens in one transaction: if anything goes wrong, nothing is
    half-loaded.
    """
    years = sorted(int(y) for y in df["year"].unique())
    existing = pd.read_sql_query(
        f"SELECT * FROM qcew_annual WHERE year IN ({','.join('?' * len(years))})",
        conn, params=years)

    # Work out which incoming rows are new, identical, or different from what's stored.
    cols_to_compare = MEASURES + ["is_suppressed", "disclosure_code"]
    merged = df.merge(existing[KEY + cols_to_compare], on=KEY, how="left",
                      suffixes=("", "_old"), indicator=True)
    is_new = merged["_merge"] == "left_only"
    existing_rows = merged.loc[~is_new]

    def differs(col):
        a, b = existing_rows[col], existing_rows[col + "_old"]
        same = (a == b) | (a.isna() & b.isna())
        return ~same.fillna(False).astype(bool)

    changed_mask = pd.Series(False, index=existing_rows.index)
    for col in cols_to_compare:
        changed_mask |= differs(col)
    n_inserted, n_changed = int(is_new.sum()), int(changed_mask.sum())
    n_unchanged = len(existing_rows) - n_changed

    records = []
    for row in df.itertuples(index=False):
        records.append((
            row.area_fips, int(row.year), row.own_code, row.industry_code, row.agglvl_code,
            _as_none(row.disclosure_code), int(row.is_suppressed),
            _as_none(row.establishments), _as_none(row.employment), _as_none(row.total_wages),
            _as_none(row.avg_weekly_wage), _as_none(row.avg_annual_pay),
            source_ids_by_year[int(row.year)], run_id))

    sql = """
        INSERT INTO qcew_annual (area_fips, year, own_code, industry_code, agglvl_code,
            disclosure_code, is_suppressed, establishments, employment, total_wages,
            avg_weekly_wage, avg_annual_pay, source_file_id, last_run_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (area_fips, year, own_code, industry_code) DO UPDATE SET
            agglvl_code = excluded.agglvl_code,
            disclosure_code = excluded.disclosure_code,
            is_suppressed = excluded.is_suppressed,
            establishments = excluded.establishments,
            employment = excluded.employment,
            total_wages = excluded.total_wages,
            avg_weekly_wage = excluded.avg_weekly_wage,
            avg_annual_pay = excluded.avg_annual_pay,
            source_file_id = excluded.source_file_id,
            last_run_id = excluded.last_run_id
    """
    try:
        conn.execute("BEGIN")
        conn.executemany(sql, records)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"inserted": n_inserted, "changed": n_changed, "unchanged": n_unchanged}


def analytic_hash(conn):
    """Fingerprint of the data itself, ignoring run ids and timestamps.

    If two runs produce the same hash, they produced the same analytical data.
    """
    h = hashlib.sha256()
    cur = conn.execute(
        """SELECT area_fips, year, own_code, industry_code, agglvl_code, disclosure_code,
                  is_suppressed, establishments, employment, total_wages, avg_weekly_wage, avg_annual_pay
           FROM qcew_annual ORDER BY area_fips, year, own_code, industry_code""")
    for row in cur:
        h.update(repr(row).encode("utf-8"))
    return h.hexdigest()


def count_rows(conn):
    return conn.execute("SELECT COUNT(*) FROM qcew_annual").fetchone()[0]
