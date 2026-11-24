"""Do the numbers in the dbt tables match the numbers in the database?

The pipeline keeps its data in SQLite. The analytics layer rebuilds summary tables from the Parquet files
with DuckDB and dbt. They are meant to say the same thing. This script asks the same questions of both,
and of pandas for the year-over-year changes, and compares the answers row by row. If two tools ever
disagree, one of them has a bug, and I want to find out which.

    python evaluation/cross_check_analytics.py

It needs a finished run first: qcew-run --publish, then qcew-build. It writes a report to
outputs/cross_check_analytics.md and exits with 0 if everything matches, 1 if something doesn't,
and 2 if it can't run.
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qcew_pipeline import load  # noqa: E402
from qcew_pipeline.config import load_config  # noqa: E402

TOLERANCE = 1e-9
EXAMPLES = 5
COUNTY_KEY = ["year", "area_fips"]
SECTOR_KEY = ["year", "area_fips", "industry_code"]
STATE_COLUMNS = ["year", "state_fips", "bls_state_employment", "county_sum_employment", "county_count", "gap_pct"]


def compare(name, left, right, key):
    """Compare two tables row by row. A missing value only matches another missing value."""
    merged = left.merge(right, on=key, how="outer", suffixes=("_db", "_dbt"), indicator=True)
    only_db = merged[merged["_merge"] == "left_only"]
    only_dbt = merged[merged["_merge"] == "right_only"]
    both = merged[merged["_merge"] == "both"]

    differences = []
    for column in [c for c in left.columns if c not in key]:
        a, b = both[f"{column}_db"], both[f"{column}_dbt"]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            same = ((a - b).abs() <= TOLERANCE * (1 + a.abs())) | (a.isna() & b.isna())
        else:
            same = (a == b) | (a.isna() & b.isna())
        for _, row in both[~same.fillna(False)].iterrows():
            where = ", ".join(f"{k}={row[k]}" for k in key)
            old, new = row[f"{column}_db"], row[f"{column}_dbt"]
            differences.append(f"{where}: {column} is {old} in the database but {new} in dbt")

    examples = differences[:EXAMPLES]
    for _, row in only_db.head(EXAMPLES).iterrows():
        examples.append("only in the database: " + ", ".join(f"{k}={row[k]}" for k in key))
    for _, row in only_dbt.head(EXAMPLES).iterrows():
        examples.append("only in dbt: " + ", ".join(f"{k}={row[k]}" for k in key))
    problems = len(differences) + len(only_db) + len(only_dbt)
    return {
        "check": name,
        "rows_compared": len(both),
        "problems": problems,
        "passed": problems == 0 and len(both) > 0,
        "examples": examples,
    }


def year_over_year(county):
    """Work out the year-over-year changes from the database's county totals, with pandas, in the same way
    the dbt model defines them: only against the previous calendar year, and never from a hidden value."""
    c = county.sort_values(["area_fips", "year"]).copy()
    consecutive = c.groupby("area_fips")["year"].shift(1) == c["year"] - 1
    prev_employment = c.groupby("area_fips")["employment"].shift(1).where(consecutive)
    prev_wage = c.groupby("area_fips")["avg_weekly_wage"].shift(1).where(consecutive)
    c["employment_change"] = c["employment"] - prev_employment
    c["employment_change_pct"] = c["employment_change"] / prev_employment.where(prev_employment > 0)
    c["avg_weekly_wage_change_pct"] = (c["avg_weekly_wage"] - prev_wage) / prev_wage.where(prev_wage > 0)
    return c[["area_fips", "year", "employment_change", "employment_change_pct", "avg_weekly_wage_change_pct"]]


def published_files_match_database(conn, lake):
    """Are the Parquet files a faithful copy of the database as it is now?"""
    manifest = json.loads((lake / "manifest.json").read_text(encoding="utf-8"))
    lake_counts = {int(y): p["rows"] for y, p in manifest["tables"]["qcew_annual"]["partitions"].items()}
    db_counts = dict(conn.execute("SELECT year, COUNT(*) FROM qcew_annual GROUP BY year").fetchall())
    problems = []
    if lake_counts != db_counts:
        problems.append(f"rows per year differ: database {db_counts}, published files {lake_counts}")
    if manifest["analytic_hash"] != load.analytic_hash(conn):
        problems.append("the database has changed since the files were published (the fingerprints differ)")
    return {
        "check": "published files match the database",
        "rows_compared": sum(db_counts.values()),
        "problems": len(problems),
        "passed": not problems and bool(db_counts),
        "examples": problems,
    }


def staging_row_count(database_rows, staging_rows):
    """Did every row in the database make it into the dbt staging table?"""
    same = database_rows == staging_rows and staging_rows > 0
    return {
        "check": "every database row reached the dbt staging table",
        "rows_compared": database_rows,
        "problems": abs(database_rows - staging_rows),
        "passed": same,
        "examples": [] if same else [f"the database has {database_rows:,} rows, dbt has {staging_rows:,}"],
    }


def run_checks(cfg):
    import duckdb  # imported here so the message below can explain a missing install

    db_path = Path(cfg["paths"]["db_path"])
    analytics_path = Path(cfg["paths"]["analytics_db"])
    lake = Path(cfg["paths"]["lake_dir"])
    needed = [
        (db_path, "Run the pipeline first: qcew-run"),
        (lake / "manifest.json", "Publish first: qcew-run --publish"),
        (analytics_path, "Build first: qcew-build"),
    ]
    for path, hint in needed:
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. {hint}")

    conn = load.connect(db_path)
    duck = duckdb.connect(str(analytics_path), read_only=True)
    try:
        county_db = pd.read_sql_query(
            "SELECT year, state_fips, area_fips, area_title, establishments, employment, avg_weekly_wage, "
            "is_suppressed FROM v_county_totals",
            conn,
        )
        sector_db = pd.read_sql_query(
            "SELECT year, state_fips, area_fips, area_title, industry_code, industry_title, employment, "
            "avg_weekly_wage, is_suppressed FROM v_county_sectors",
            conn,
        )
        state_totals_db = pd.read_sql_query(
            "SELECT year, substr(area_fips, 1, 2) AS state_fips, employment AS bls_state_employment "
            "FROM qcew_annual WHERE agglvl_code = '51' AND industry_code = '10'",
            conn,
        )
        total_rows_db = conn.execute("SELECT COUNT(*) FROM qcew_annual").fetchone()[0]

        county_dbt = duck.execute(
            "SELECT year, state_fips, area_fips, area_title, establishments, employment, avg_weekly_wage, "
            "is_suppressed::INTEGER AS is_suppressed FROM mart_county_year"
        ).df()
        sector_dbt = duck.execute(
            "SELECT year, state_fips, area_fips, area_title, industry_code, industry_title, employment, "
            "avg_weekly_wage, is_suppressed::INTEGER AS is_suppressed FROM mart_county_sector_year"
        ).df()
        state_dbt = duck.execute(
            "SELECT year, state_fips, bls_state_employment, county_sum_employment, county_count, gap_pct "
            "FROM mart_state_year"
        ).df()
        changes_dbt = duck.execute(
            "SELECT area_fips, year, employment_change, employment_change_pct, avg_weekly_wage_change_pct "
            "FROM mart_county_year"
        ).df()
        staging_rows = duck.execute("SELECT COUNT(*) FROM stg_qcew_annual").fetchone()[0]

        sums = (
            county_db.groupby(["state_fips", "year"])
            .agg(
                county_sum_employment=("employment", lambda s: s.sum(min_count=1)),
                county_count=("area_fips", "count"),
            )
            .reset_index()
        )
        state_db = state_totals_db.merge(sums, on=["state_fips", "year"], how="left")
        state_db["gap_pct"] = (state_db["county_sum_employment"] - state_db["bls_state_employment"]) / state_db[
            "bls_state_employment"
        ].where(state_db["bls_state_employment"] > 0)
        state_db = state_db[STATE_COLUMNS]

        results = [
            published_files_match_database(conn, lake),
            staging_row_count(total_rows_db, staging_rows),
            compare("county totals: database view vs dbt table", county_db, county_dbt, COUNTY_KEY),
            compare("county sectors: database view vs dbt table", sector_db, sector_dbt, SECTOR_KEY),
            compare("state totals and county sums: pandas vs dbt table", state_db, state_dbt, ["year", "state_fips"]),
            compare("year-over-year changes: pandas vs dbt table", year_over_year(county_db), changes_dbt, COUNTY_KEY),
        ]
    finally:
        duck.close()
        conn.close()
    return results


def report(results):
    lines = [
        "# Cross-check: database, pandas and dbt",
        "",
        "| Check | Rows compared | Differences | Result |",
        "|---|---|---|---|",
    ]
    for r in results:
        result = "PASS" if r["passed"] else "FAIL"
        lines.append(f"| {r['check']} | {r['rows_compared']:,} | {r['problems']:,} | {result} |")
    failing = [r for r in results if not r["passed"]]
    if failing:
        lines += ["", "## What did not match", ""]
        for r in failing:
            lines.append(f"**{r['check']}**")
            lines += [f"- {example}" for example in r["examples"]] or ["- (no rows were compared)"]
            lines.append("")
    else:
        lines += ["", "Everything matches: the same numbers come out of the database, pandas and dbt."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that the dbt tables match the database.")
    parser.add_argument("--config", help="path to a config.toml (default: the one inside the package)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    try:
        results = run_checks(cfg)
    except ModuleNotFoundError as err:
        print(f'{err.name} isn\'t installed. Install the extras with: pip install -e ".[analytics]"', file=sys.stderr)
        return 2
    except FileNotFoundError as err:
        print(err, file=sys.stderr)
        return 2

    text = report(results)
    out = Path(cfg["paths"]["output_dir"]) / "cross_check_analytics.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Report saved to {out}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
