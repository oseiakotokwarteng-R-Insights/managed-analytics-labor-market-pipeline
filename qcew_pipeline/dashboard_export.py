"""Step 10: write the numbers the dashboard shows, as small JSON files.

The dashboard (the dashboard folder) is plain HTML, CSS and JavaScript. It doesn't talk to a database. It
reads the files this step writes, so it can be put on any web host and opened by anyone.

    qcew-dashboard                       # writes dashboard/data/
    qcew-dashboard --out some/folder

Everything comes from the SQLite database, so run qcew-run first. The second source (County Business
Patterns) is optional: if qcew-cbp has been run, its comparison is included too.

The files:

    meta.json          the region, the years, when this was made, the last run, and the source files
    counties.json      jobs, weekly wage and businesses for every county, year by year
    sectors/<fips>.json  the same by industry sector, one small file per county, loaded when you open a county
    states.json        each state's total from BLS next to its counties added up, with the unknown bucket
    checks.json        the validation results from the last run, and the history of runs
    cbp.json           QCEW compared with County Business Patterns (only if it has been run)

A number BLS hid stays null in these files. It's never turned into a zero, so the dashboard can say "hidden".
"""

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import load
from .config import add_region_argument, load_config
from .publish import _swap

DEFAULT_OUT = Path("dashboard") / "data"
HISTORY_RUNS = 10


class DashboardError(Exception):
    """Raised when there is nothing to show yet (no database, or nothing in it)."""


def _num(value):
    """A database number, ready for JSON: whole numbers lose the '.0', and missing stays null."""
    if value is None:
        return None
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        if value.is_integer():
            return int(value)
        return round(value, 2)
    return value


def _state_name(title):
    return title.replace(" -- Statewide", "").strip()


def _connect(cfg):
    db = Path(cfg["paths"]["db_path"])
    if not db.is_file():
        raise DashboardError(f"There is no database at {db}. Run qcew-run first.")
    conn = load.connect(db)
    conn.row_factory = sqlite3.Row
    load.init_schema(conn)  # a database made by an older version still has to answer the queries below
    return conn


def _years(conn, cfg):
    states = list(cfg["state_fips"])
    marks = ",".join("?" for _ in states)
    rows = conn.execute(
        f"""SELECT DISTINCT q.year FROM qcew_annual q JOIN dim_area a ON a.area_fips = q.area_fips
            WHERE a.state_fips IN ({marks}) AND q.own_code = '5' ORDER BY q.year""",
        states,
    ).fetchall()
    return [int(r["year"]) for r in rows]


def _by_year(rows, years, fields):
    """Rows for one place or industry, as lists lined up with `years` (null where a year is missing)."""
    index = {int(r["year"]): r for r in rows}
    out: dict[str, list] = {name: [] for name in fields}
    out["hidden"] = []
    for year in years:
        row = index.get(year)
        for name in fields:
            out[name].append(_num(row[name]) if row is not None else None)
        out["hidden"].append(1 if row is not None and row["is_suppressed"] else 0)
    return out


# ---------------------------------------------------------------- the pieces
def _states(conn, cfg):
    marks = ",".join("?" for _ in cfg["state_fips"])
    rows = conn.execute(
        f"SELECT area_fips, area_title, state_fips FROM dim_area WHERE area_type = 'state' AND state_fips IN ({marks}) "
        "ORDER BY state_fips",
        list(cfg["state_fips"]),
    ).fetchall()
    return [{"fips": r["state_fips"], "name": _state_name(r["area_title"])} for r in rows]


def _meta(conn, cfg, years, states):
    run = conn.execute(
        """SELECT * FROM pipeline_runs WHERE status IN ('success', 'failed_validation', 'failed')
           ORDER BY run_id DESC LIMIT 1"""
    ).fetchone()
    last = None
    if run is not None:
        last = {
            key: _num(run[key])
            for key in (
                "run_id",
                "started_at",
                "finished_at",
                "status",
                "rows_kept",
                "rows_inserted",
                "rows_changed",
                "rows_unchanged",
                "rows_removed",
                "validation_errors",
                "validation_warnings",
                "analytic_hash",
            )
        }
    sources = conn.execute(
        """SELECT s.year, s.sha256, s.size_bytes, s.retrieved_at, s.rows_scanned, s.rows_kept
           FROM source_files s
           JOIN (SELECT year, MAX(source_file_id) AS newest FROM source_files GROUP BY year) n
             ON n.newest = s.source_file_id
           ORDER BY s.year"""
    ).fetchall()
    counts = conn.execute(
        """SELECT COUNT(*) AS rows_total,
                  SUM(CASE WHEN a.area_type = 'county' AND q.agglvl_code = '71' THEN 1 ELSE 0 END) AS county_rows,
                  SUM(CASE WHEN q.is_suppressed = 1 THEN 1 ELSE 0 END) AS hidden_rows
           FROM qcew_annual q JOIN dim_area a ON a.area_fips = q.area_fips WHERE q.own_code = '5'"""
    ).fetchone()
    return {
        "region": cfg.get("region"),
        "region_name": cfg.get("region_name"),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "years": years,
        "latest_year": years[-1],
        "states": states,
        "rows_in_database": _num(counts["rows_total"]),
        "hidden_rows": _num(counts["hidden_rows"]),
        "last_run": last,
        "sources": [{k: _num(s[k]) for k in s.keys()} for s in sources],
        "has_cbp": conn.execute("SELECT COUNT(*) FROM cbp_county").fetchone()[0] > 0,
    }


def _counties(conn, cfg, years):
    marks = ",".join("?" for _ in cfg["state_fips"])
    rows = conn.execute(
        f"""SELECT q.area_fips, a.area_title, a.state_fips, q.year, q.employment, q.avg_weekly_wage,
                   q.establishments, q.is_suppressed
            FROM qcew_annual q JOIN dim_area a ON a.area_fips = q.area_fips
            WHERE q.own_code = '5' AND q.agglvl_code = '71' AND a.area_type = 'county' AND a.state_fips IN ({marks})
            ORDER BY q.area_fips, q.year""",
        list(cfg["state_fips"]),
    ).fetchall()
    grouped: dict[str, list] = {}
    titles: dict[str, tuple[str, str]] = {}
    for r in rows:
        grouped.setdefault(r["area_fips"], []).append(r)
        titles[r["area_fips"]] = (r["area_title"], r["state_fips"])
    counties = []
    for fips, county_rows in grouped.items():
        series = _by_year(county_rows, years, ["employment", "avg_weekly_wage", "establishments"])
        counties.append(
            {
                "fips": fips,
                "title": titles[fips][0],
                "state": titles[fips][1],
                "employment": series["employment"],
                "wage": series["avg_weekly_wage"],
                "establishments": series["establishments"],
                "hidden": series["hidden"],
            }
        )
    return {"years": years, "counties": counties}


def _sectors(conn, cfg, years):
    """One entry per county: its sectors, year by year. Returns {fips: {...}}."""
    marks = ",".join("?" for _ in cfg["state_fips"])
    rows = conn.execute(
        f"""SELECT q.area_fips, a.area_title, q.industry_code, i.industry_title, q.year, q.employment,
                   q.avg_weekly_wage, q.establishments, q.is_suppressed
            FROM qcew_annual q
            JOIN dim_area a ON a.area_fips = q.area_fips
            JOIN dim_industry i ON i.industry_code = q.industry_code
            WHERE q.own_code = '5' AND q.agglvl_code = '74' AND a.area_type = 'county' AND a.state_fips IN ({marks})
            ORDER BY q.area_fips, q.industry_code, q.year""",
        list(cfg["state_fips"]),
    ).fetchall()
    by_county: dict[str, dict] = {}
    for r in rows:
        county = by_county.setdefault(r["area_fips"], {"title": r["area_title"], "sectors": {}})
        county["sectors"].setdefault(r["industry_code"], {"title": r["industry_title"], "rows": []})["rows"].append(r)
    out = {}
    for fips, county in by_county.items():
        sectors = []
        for code, sector in county["sectors"].items():
            series = _by_year(sector["rows"], years, ["employment", "avg_weekly_wage", "establishments"])
            sectors.append(
                {
                    "code": code,
                    "title": sector["title"],
                    "employment": series["employment"],
                    "wage": series["avg_weekly_wage"],
                    "establishments": series["establishments"],
                    "hidden": series["hidden"],
                }
            )
        out[fips] = {"fips": fips, "title": county["title"], "years": years, "sectors": sectors}
    return out


def _state_table(conn, cfg, years, states):
    """Each state's BLS total, its named counties added up, and the 'unknown or undefined' bucket."""
    marks = ",".join("?" for _ in cfg["state_fips"])
    rows = conn.execute(
        f"""SELECT a.state_fips, a.area_type, q.year, q.employment, q.is_suppressed
            FROM qcew_annual q JOIN dim_area a ON a.area_fips = q.area_fips
            WHERE q.own_code = '5' AND q.agglvl_code IN ('51', '71') AND a.state_fips IN ({marks})""",
        list(cfg["state_fips"]),
    ).fetchall()
    table: dict[tuple[str, int], dict] = {}
    for r in rows:
        cell = table.setdefault(
            (r["state_fips"], int(r["year"])),
            {"state": None, "counties": 0.0, "n": 0, "hidden": 0, "bucket": None, "bucket_row": False},
        )
        if r["area_type"] == "state":
            cell["state"] = r["employment"]
        elif r["area_type"] == "county":
            cell["n"] += 1
            if r["employment"] is None:
                cell["hidden"] += 1
            else:
                cell["counties"] += r["employment"]
        elif r["area_type"] == "undefined":
            cell["bucket_row"] = True
            cell["bucket"] = r["employment"]
    names = {s["fips"]: s["name"] for s in states}
    out = []
    for (fips, year), cell in sorted(table.items()):
        state_total, named, bucket = cell["state"], cell["counties"], cell["bucket"]
        # A gap only means something when every number it uses is shown. A hidden number leaves it empty.
        counties_complete = state_total is not None and state_total != 0 and cell["n"] > 0 and cell["hidden"] == 0
        bucket_hidden = cell["bucket_row"] and bucket is None
        out.append(
            {
                "state": fips,
                "name": names.get(fips, fips),
                "year": year,
                "state_total": _num(state_total),
                "named_counties": _num(named) if cell["n"] else None,
                "unknown_bucket": _num(bucket),
                "unknown_bucket_hidden": bucket_hidden,
                "counties_hidden": cell["hidden"],
                "gap_without_bucket_pct": _gap_pct(named, state_total) if counties_complete else None,
                "gap_with_bucket_pct": (
                    _gap_pct(named + (bucket or 0), state_total) if counties_complete and not bucket_hidden else None
                ),
                "bucket_share_pct": _share_pct(bucket, state_total),
            }
        )
    return {"years": years, "rows": out}


def _gap_pct(value, state_total):
    """How far `value` is from the state total, as a percent of the total."""
    return _num(round(100 * (value - state_total) / state_total, 2))


def _share_pct(value, whole):
    """`value` as a percent of `whole`, or null if either is missing."""
    if value is None or not whole:
        return None
    return _num(round(100 * value / whole, 2))


def _checks(conn):
    run = conn.execute("SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT 1").fetchone()
    results = []
    if run is not None:
        results = [
            {
                "name": r["check_name"],
                "level": r["level"],
                "passed": bool(r["passed"]),
                "failed_count": _num(r["failed_count"]),
                "detail": r["detail"],
            }
            for r in conn.execute(
                "SELECT * FROM validation_results WHERE run_id = ? ORDER BY id", (run["run_id"],)
            ).fetchall()
        ]
    history = conn.execute(
        f"""SELECT run_id, started_at, finished_at, status, years, rows_inserted, rows_changed, rows_unchanged,
                   rows_removed, validation_errors, validation_warnings
            FROM pipeline_runs ORDER BY run_id DESC LIMIT {HISTORY_RUNS}"""
    ).fetchall()
    return {
        "run_id": _num(run["run_id"]) if run is not None else None,
        "checks": results,
        "history": [{k: _num(h[k]) for k in h.keys()} for h in history],
    }


def _cbp(conn):
    """The comparison with County Business Patterns, or None if it hasn't been run."""
    if conn.execute("SELECT COUNT(*) FROM cbp_county").fetchone()[0] == 0:
        return None
    latest = conn.execute("SELECT MAX(compared_at) FROM cross_source_results").fetchone()[0]
    results = []
    if latest:
        results = [
            {"name": r["check_name"], "level": r["level"], "passed": bool(r["passed"]), "detail": r["detail"]}
            for r in conn.execute(
                "SELECT * FROM cross_source_results WHERE compared_at = ? ORDER BY id", (latest,)
            ).fetchall()
        ]
    ratios = conn.execute(
        """SELECT v.state_fips, v.year, SUM(v.qcew_employment) AS qcew, SUM(v.cbp_employment) AS cbp
           FROM v_qcew_vs_cbp v
           WHERE v.industry_code = '10' AND v.qcew_suppressed = 0 AND v.cbp_withheld = 0
             AND v.qcew_employment IS NOT NULL AND v.cbp_employment IS NOT NULL
           GROUP BY v.state_fips, v.year ORDER BY v.state_fips, v.year"""
    ).fetchall()
    return {
        "compared_at": latest,
        "results": results,
        "state_ratios": [
            {
                "state": r["state_fips"],
                "year": int(r["year"]),
                "qcew_employment": _num(r["qcew"]),
                "cbp_employment": _num(r["cbp"]),
                "ratio": _num(round(r["cbp"] / r["qcew"], 3)) if r["qcew"] else None,
            }
            for r in ratios
        ],
    }


# ---------------------------------------------------------------- writing
def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False makes a stray NaN fail here, not in someone's browser.
    text = json.dumps(data, separators=(",", ":"), allow_nan=False, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def export_dashboard(cfg, out_dir=None):
    """Write every file the dashboard needs. Returns a small summary. Raises DashboardError if there's nothing to show.

    The files are written next to the old folder and swapped in at the end, so anyone looking at the
    dashboard never sees half of an update.
    """
    final_dir = Path(out_dir) if out_dir else Path.cwd() / DEFAULT_OUT
    new_dir = final_dir.with_name(final_dir.name + ".new")
    conn = _connect(cfg)
    try:
        years = _years(conn, cfg)
        if not years:
            raise DashboardError("The database has no private-sector rows for these states yet. Run qcew-run first.")
        states = _states(conn, cfg)
        meta = _meta(conn, cfg, years, states)
        counties = _counties(conn, cfg, years)
        sectors = _sectors(conn, cfg, years)
        state_table = _state_table(conn, cfg, years, states)
        checks = _checks(conn)
        cbp = _cbp(conn)
    finally:
        conn.close()

    if new_dir.exists():
        shutil.rmtree(new_dir)
    _write(new_dir / "meta.json", meta)
    _write(new_dir / "counties.json", counties)
    for fips, data in sectors.items():
        _write(new_dir / "sectors" / f"{fips}.json", data)
    _write(new_dir / "states.json", state_table)
    _write(new_dir / "checks.json", checks)
    if cbp is not None:
        _write(new_dir / "cbp.json", cbp)
    _swap(new_dir, final_dir)
    return {
        "folder": str(final_dir),
        "years": years,
        "counties": len(counties["counties"]),
        "sector_files": len(sectors),
        "has_cbp": cbp is not None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the files the dashboard reads, from the database.")
    parser.add_argument("--config", help="path to a config.toml (default: the one inside the package)")
    add_region_argument(parser)
    parser.add_argument("--out", help="folder for the files (default: dashboard/data in the folder you run this from)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config, args.region)
    try:
        summary = export_dashboard(cfg, args.out)
    except DashboardError as err:
        print(err, file=sys.stderr)
        return 2
    span = f"{summary['years'][0]}-{summary['years'][-1]}"
    extra = " Includes the comparison with County Business Patterns." if summary["has_cbp"] else ""
    print(f"Wrote the dashboard data for {summary['counties']} counties, {span}, to {summary['folder']}.{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
