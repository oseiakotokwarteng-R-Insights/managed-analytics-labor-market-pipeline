"""Step 6: publish the data as Parquet files.

The SQLite database is where the pipeline keeps its books: the run history, the checks, the
rows BLS removed. Parquet is a different kind of file. It stores data column by column, which
is what analytics tools (DuckDB, pandas, R, Spark) read fastest. So after a run I write the
data out as Parquet too, and the analytics layer reads those files instead of the database.

Layout (the year folder is a Hive-style partition, so tools can skip years they don't need):

    data/lake/
        qcew_annual/year=2024/data.parquet     one folder per year
        qcew_annual/year=2025/data.parquet
        dim_area.parquet                        the code lists, with names
        dim_industry.parquet
        dim_ownership.parquet
        dim_agglevel.parquet
        manifest.json                           what is in here, and which database state it came from

Each publish rebuilds the whole folder next to the old one and then swaps them, so anyone
reading the files never sees a half-written set. Hidden (suppressed) values stay empty, never zero.

    python -m qcew_pipeline.publish
    python -m qcew_pipeline.publish --out some/other/folder
"""

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path

from . import load
from .config import load_config

# year comes first because the rows are grouped by it; it ends up in the folder name, not in the file.
FACT_COLUMNS = [
    ("area_fips", "VARCHAR"),
    ("own_code", "VARCHAR"),
    ("industry_code", "VARCHAR"),
    ("agglvl_code", "VARCHAR"),
    ("disclosure_code", "VARCHAR"),
    ("is_suppressed", "INTEGER"),
    ("establishments", "DOUBLE"),
    ("employment", "DOUBLE"),
    ("total_wages", "DOUBLE"),
    ("avg_weekly_wage", "DOUBLE"),
    ("avg_annual_pay", "DOUBLE"),
    ("source_sha256", "VARCHAR"),
]

FACT_SQL = """
    SELECT q.year, q.area_fips, q.own_code, q.industry_code, q.agglvl_code, q.disclosure_code,
           q.is_suppressed, q.establishments, q.employment, q.total_wages,
           q.avg_weekly_wage, q.avg_annual_pay, s.sha256
    FROM qcew_annual q
    LEFT JOIN source_files s ON s.source_file_id = q.source_file_id
    ORDER BY q.year, q.area_fips, q.own_code, q.industry_code"""

DIMENSIONS = {
    "dim_area": (
        [("area_fips", "VARCHAR"), ("area_title", "VARCHAR"), ("state_fips", "VARCHAR"), ("area_type", "VARCHAR")],
        "SELECT area_fips, area_title, state_fips, area_type FROM dim_area ORDER BY area_fips",
    ),
    "dim_industry": (
        [("industry_code", "VARCHAR"), ("industry_title", "VARCHAR")],
        "SELECT industry_code, industry_title FROM dim_industry ORDER BY industry_code",
    ),
    "dim_ownership": (
        [("own_code", "VARCHAR"), ("own_title", "VARCHAR")],
        "SELECT own_code, own_title FROM dim_ownership ORDER BY own_code",
    ),
    "dim_agglevel": (
        [("agglvl_code", "VARCHAR"), ("agglvl_title", "VARCHAR")],
        "SELECT agglvl_code, agglvl_title FROM dim_agglevel ORDER BY agglvl_code",
    ),
}


def _write_parquet(duck, columns, rows, path):
    """Put the rows in a small DuckDB table with explicit types, then copy it out as a Parquet file.

    Going through a typed table (instead of a pandas frame) means a missing value is always a real
    NULL, and the column types never depend on what pandas guessed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    duck.execute("DROP TABLE IF EXISTS to_write")
    duck.execute(f"CREATE TABLE to_write ({', '.join(f'{name} {kind}' for name, kind in columns)})")
    duck.executemany(f"INSERT INTO to_write VALUES ({','.join('?' * len(columns))})", rows)
    target = path.as_posix().replace("'", "''")
    duck.execute(f"COPY to_write TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    duck.execute("DROP TABLE to_write")


def _swap(new_dir, final_dir):
    """Replace final_dir with new_dir, keeping the old one until the new one is in place."""
    old_dir = final_dir.with_name(final_dir.name + ".old")
    if old_dir.exists():
        shutil.rmtree(old_dir)
    if final_dir.exists():
        final_dir.rename(old_dir)
    new_dir.rename(final_dir)
    if old_dir.exists():
        shutil.rmtree(old_dir)


def publish_lake(cfg, out_dir=None):
    """Write the whole database out as Parquet files. Returns the manifest (also saved as manifest.json)."""
    import duckdb  # imported here so the rest of the pipeline works without it

    final_dir = Path(out_dir or cfg["paths"]["lake_dir"])
    new_dir = final_dir.with_name(final_dir.name + ".new")
    if new_dir.exists():
        shutil.rmtree(new_dir)

    conn = load.connect(cfg["paths"]["db_path"])
    try:
        fact_rows = conn.execute(FACT_SQL).fetchall()
        dim_rows = {name: conn.execute(sql).fetchall() for name, (_, sql) in DIMENSIONS.items()}
        digest = load.analytic_hash(conn)
    finally:
        conn.close()
    if not fact_rows:
        raise ValueError("The database has no data to publish. Run the pipeline first.")

    manifest = {
        "published_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_database": Path(cfg["paths"]["db_path"]).name,
        "analytic_hash": digest,
        "tables": {},
    }
    duck = duckdb.connect()
    try:
        partitions = {}
        for year, group in groupby(fact_rows, key=lambda row: row[0]):
            rows = [row[1:] for row in group]
            relative = f"qcew_annual/year={int(year)}/data.parquet"
            _write_parquet(duck, FACT_COLUMNS, rows, new_dir / relative)
            partitions[str(int(year))] = {"rows": len(rows), "file": relative}
        manifest["tables"]["qcew_annual"] = {
            "rows": sum(p["rows"] for p in partitions.values()),
            "partitions": partitions,
        }
        for name, (columns, _) in DIMENSIONS.items():
            relative = f"{name}.parquet"
            _write_parquet(duck, columns, dim_rows[name], new_dir / relative)
            manifest["tables"][name] = {"rows": len(dim_rows[name]), "file": relative}
    finally:
        duck.close()

    (new_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _swap(new_dir, final_dir)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish the QCEW database as Parquet files.")
    parser.add_argument("--config", help="path to a config.toml (default: the one inside the package)")
    parser.add_argument("--out", help="folder for the Parquet files (default: lake_dir in the settings)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    out = Path(args.out) if args.out else cfg["paths"]["lake_dir"]
    try:
        manifest = publish_lake(cfg, out)
    except ModuleNotFoundError as err:
        print(f"{err.name} isn't installed. Install the analytics extras with: pip install -e \".[analytics]\"",
              file=sys.stderr)
        return 2
    except ValueError as err:
        print(err, file=sys.stderr)
        return 1
    rows = manifest["tables"]["qcew_annual"]["rows"]
    years = sorted(manifest["tables"]["qcew_annual"]["partitions"])
    print(f"Published {rows:,} rows for {years[0]}-{years[-1]} to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
