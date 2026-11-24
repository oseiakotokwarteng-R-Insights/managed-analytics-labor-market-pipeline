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
from datetime import UTC, datetime
from pathlib import Path

from .transform import MEASURES, area_type

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


def now_iso():
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    _add_missing_columns(conn)
    conn.commit()


def _add_missing_columns(conn):
    """Bring a database made by an older version up to date.

    CREATE TABLE IF NOT EXISTS leaves an existing table alone, so a new column has to be added by hand.
    """
    have = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_runs)")}
    if "rows_removed" not in have:
        conn.execute("ALTER TABLE pipeline_runs ADD COLUMN rows_removed INTEGER")


# ---------------------------------------------------------------- run bookkeeping
def start_run(conn, years):
    cur = conn.execute(
        "INSERT INTO pipeline_runs (started_at, status, years) VALUES (?, 'running', ?)",
        (now_iso(), json.dumps(list(years))),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id, status, **fields):
    """Close out a run. Any extra keyword args must be column names of pipeline_runs."""
    allowed = {
        "rows_scanned",
        "rows_kept",
        "rows_inserted",
        "rows_changed",
        "rows_unchanged",
        "rows_removed",
        "validation_errors",
        "validation_warnings",
        "analytic_hash",
        "notes",
    }
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
        (
            run_id,
            info["year"],
            info["url"],
            info["local_name"],
            info["retrieved_at"],
            info["download_status"],
            info["size_bytes"],
            info["sha256"],
            rows_scanned,
            rows_kept,
        ),
    )
    conn.commit()
    return cur.lastrowid


def record_validation(conn, run_id, results):
    conn.executemany(
        """INSERT INTO validation_results (run_id, check_name, level, passed, failed_count, detail)
           VALUES (?,?,?,?,?,?)""",
        [(run_id, r.name, r.level, int(r.passed), r.failed_count, r.detail) for r in results],
    )
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
        area[["area_fips", "area_title", "state_fips", "area_type"]].itertuples(index=False, name=None),
    )

    conn.executemany(
        "INSERT OR REPLACE INTO dim_industry VALUES (?,?)",
        refs["industry"][["industry_code", "industry_title"]].itertuples(index=False, name=None),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO dim_ownership VALUES (?,?)",
        refs["ownership"][["own_code", "own_title"]].itertuples(index=False, name=None),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO dim_agglevel VALUES (?,?)",
        refs["agglevel"][["agglvl_code", "agglvl_title"]].itertuples(index=False, name=None),
    )
    conn.commit()


# ---------------------------------------------------------------- the main load
class TooManyRemovedError(Exception):
    """Raised when a load would remove an unusually large share of the stored rows."""


KEY = ["area_fips", "year", "own_code", "industry_code"]
STAGING_COLUMNS = (
    ["area_fips", "year", "own_code", "industry_code", "agglvl_code", "disclosure_code", "is_suppressed"]
    + MEASURES
    + ["source_file_id"]
)
# Rows are copied into the staging table in pieces of this size, so memory stays flat.
STAGING_CHUNK = 50_000

_SAME_KEY = " AND ".join(f"{{a}}.{c} = {{b}}.{c}" for c in KEY)


def _same_key(a, b):
    return _SAME_KEY.format(a=a, b=b)


def _scope_filter(years, cfg):
    """SQL and parameters that select the stored rows this run is responsible for:
    the years it loaded, and only the states, ownership and levels in the config.
    Narrowing the config later must never look like BLS deleting data."""
    states, own, levels = cfg["state_fips"], cfg["ownership_codes"], cfg["agglvl_codes"]
    sql = (
        f"year IN ({','.join('?' * len(years))}) "
        f"AND substr(area_fips, 1, 2) IN ({','.join('?' * len(states))}) "
        f"AND own_code IN ({','.join('?' * len(own))}) "
        f"AND agglvl_code IN ({','.join('?' * len(levels))})"
    )
    return sql, [*years, *states, *own, *levels]


def count_in_scope(conn, years, cfg):
    """How many stored rows fall inside the run's years and configured scope."""
    sql, params = _scope_filter(years, cfg)
    # NOT INDEXED: reading the rows in order is faster here than looking each one up through an index.
    return conn.execute(f"SELECT COUNT(*) FROM qcew_annual NOT INDEXED WHERE {sql}", params).fetchone()[0]


def _stage(conn, df, source_ids_by_year):
    """Copy the incoming rows into a temporary table inside the database, in chunks.

    From here on, comparing against what is stored is done by SQLite itself, so the stored
    table never has to be read into pandas.
    """
    conn.execute("DROP TABLE IF EXISTS temp.stg_qcew_annual")
    conn.execute("""
        CREATE TEMP TABLE stg_qcew_annual (
            area_fips TEXT NOT NULL, year INTEGER NOT NULL, own_code TEXT NOT NULL,
            industry_code TEXT NOT NULL, agglvl_code TEXT NOT NULL, disclosure_code TEXT,
            is_suppressed INTEGER NOT NULL, establishments REAL, employment REAL, total_wages REAL,
            avg_weekly_wage REAL, avg_annual_pay REAL, source_file_id INTEGER,
            PRIMARY KEY (area_fips, year, own_code, industry_code))""")
    staged = df.copy()
    staged["source_file_id"] = staged["year"].map(lambda y: source_ids_by_year[int(y)])
    staged["year"] = staged["year"].astype("int64")
    staged = staged[STAGING_COLUMNS]
    # Python objects with None for missing values: what sqlite3 expects (it can't bind pandas NA).
    staged = staged.astype(object).where(staged.notna(), None)
    placeholders = ",".join("?" * len(STAGING_COLUMNS))
    sql = f"INSERT INTO temp.stg_qcew_annual ({', '.join(STAGING_COLUMNS)}) VALUES ({placeholders})"
    for start in range(0, len(staged), STAGING_CHUNK):
        chunk = staged.iloc[start : start + STAGING_CHUNK]
        conn.executemany(sql, chunk.itertuples(index=False, name=None))


def _count(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def load_facts(conn, df, run_id, source_ids_by_year, cfg):
    """Upsert the standardized rows. Returns counts of inserted / changed / unchanged / removed rows.

    How it works: the new rows go into a temporary staging table, and SQLite does the rest
    (what is new, what changed, what BLS no longer publishes, and the upsert itself).

    "Removed" means a row that was stored from an earlier run but is no longer in what BLS
    published. Those rows are copied to qcew_annual_removed and taken out of qcew_annual.
    If that would remove too many rows (see max_removed_fraction in config.toml), nothing
    is changed and TooManyRemovedError is raised.

    Everything happens in one transaction: if anything goes wrong, nothing is
    half-loaded.
    """
    years = sorted(int(y) for y in df["year"].unique())
    scope_sql, scope_params = _scope_filter(years, cfg)
    max_fraction = cfg.get("load", {}).get("max_removed_fraction", 0.05)
    same_as_staged = f"EXISTS (SELECT 1 FROM temp.stg_qcew_annual s WHERE {_same_key('s', 'qcew_annual')})"
    differs = " OR ".join(f"s.{c} IS NOT q.{c}" for c in MEASURES + ["is_suppressed", "disclosure_code"])

    try:
        conn.execute("BEGIN")
        _stage(conn, df, source_ids_by_year)

        # ---- work out what this load will do, before changing anything ----
        n_staged = _count(conn, "SELECT COUNT(*) FROM temp.stg_qcew_annual")
        n_inserted = _count(
            conn,
            f"""
            SELECT COUNT(*) FROM temp.stg_qcew_annual s
            WHERE NOT EXISTS (SELECT 1 FROM qcew_annual q WHERE {_same_key("s", "q")})""",
        )
        n_changed = _count(
            conn,
            f"""
            SELECT COUNT(*) FROM temp.stg_qcew_annual s
            JOIN qcew_annual q ON {_same_key("s", "q")} WHERE {differs}""",
        )
        n_unchanged = n_staged - n_inserted - n_changed
        # One pass over the stored rows in scope: how many there are, and how many are no longer published.
        stored_in_scope, n_removed = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(NOT {same_as_staged}), 0) FROM qcew_annual NOT INDEXED WHERE {scope_sql}",
            scope_params,
        ).fetchone()
        if stored_in_scope and n_removed / stored_in_scope > max_fraction:
            raise TooManyRemovedError(
                f"{n_removed:,} of {stored_in_scope:,} stored rows ({n_removed / stored_in_scope:.1%}) are missing "
                f"from the new data, more than the {max_fraction:.0%} limit. Nothing was changed."
            )

        # ---- rows BLS stopped publishing: archive, then remove ----
        if n_removed:
            conn.execute(
                f"""
                INSERT INTO qcew_annual_removed (area_fips, year, own_code, industry_code, agglvl_code,
                       disclosure_code, is_suppressed, establishments, employment, total_wages,
                       avg_weekly_wage, avg_annual_pay, source_file_id, last_run_id, removed_run_id, removed_at)
                SELECT area_fips, year, own_code, industry_code, agglvl_code,
                       disclosure_code, is_suppressed, establishments, employment, total_wages,
                       avg_weekly_wage, avg_annual_pay, source_file_id, last_run_id, ?, ?
                FROM qcew_annual NOT INDEXED WHERE {scope_sql} AND NOT {same_as_staged}""",
                [run_id, now_iso(), *scope_params],
            )
            conn.execute(
                f"DELETE FROM qcew_annual NOT INDEXED WHERE {scope_sql} AND NOT {same_as_staged}", scope_params
            )

        # ---- insert new rows and update existing ones ----
        conn.execute(
            """
            INSERT INTO qcew_annual (area_fips, year, own_code, industry_code, agglvl_code,
                disclosure_code, is_suppressed, establishments, employment, total_wages,
                avg_weekly_wage, avg_annual_pay, source_file_id, last_run_id)
            SELECT area_fips, year, own_code, industry_code, agglvl_code,
                disclosure_code, is_suppressed, establishments, employment, total_wages,
                avg_weekly_wage, avg_annual_pay, source_file_id, ?
            FROM temp.stg_qcew_annual WHERE true
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
                last_run_id = excluded.last_run_id""",
            [run_id],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("DROP TABLE IF EXISTS temp.stg_qcew_annual")

    return {"inserted": n_inserted, "changed": n_changed, "unchanged": n_unchanged, "removed": n_removed}


def analytic_hash(conn):
    """Fingerprint of the data itself, ignoring run ids and timestamps.

    If two runs produce the same hash, they produced the same analytical data.
    """
    h = hashlib.sha256()
    cur = conn.execute(
        """SELECT area_fips, year, own_code, industry_code, agglvl_code, disclosure_code,
                  is_suppressed, establishments, employment, total_wages, avg_weekly_wage, avg_annual_pay
           FROM qcew_annual ORDER BY area_fips, year, own_code, industry_code"""
    )
    for row in cur:
        h.update(repr(row).encode("utf-8"))
    return h.hexdigest()


def count_rows(conn):
    return conn.execute("SELECT COUNT(*) FROM qcew_annual").fetchone()[0]
