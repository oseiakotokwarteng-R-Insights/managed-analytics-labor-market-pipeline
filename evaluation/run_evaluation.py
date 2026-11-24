"""Tests the pipeline itself (separate from the manual-vs-automated comparison).

I want to answer four plain questions with evidence instead of just saying "it works":

  1. Does a cold run (nothing downloaded yet) finish, and how long does it take?
  2. If I run it again, do I get the same data with no duplicates?
  3. If I rebuild the database from scratch, do I get exactly the same data?
  4. When I put mistakes in on purpose, or things go wrong, does the pipeline notice?

Run it from the project folder:

    python evaluation/run_evaluation.py

It writes outputs/evaluation_results.json and outputs/evaluation_summary.md.
It downloads the real BLS files on the cold run, so it takes a few minutes.
"""

import copy
import json
import platform
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qcew_pipeline import collect, load, transform, validate  # noqa: E402
from qcew_pipeline.config import load_config  # noqa: E402
from qcew_pipeline.run import run_pipeline, setup_logging  # noqa: E402
from qcew_pipeline.years import resolve_years  # noqa: E402


def row_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM qcew_annual").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def delete_db(cfg):
    p = Path(cfg["paths"]["db_path"])
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(p) + suffix).unlink(missing_ok=True)


def slim(result):
    """Keep the parts of a run result that are worth writing down."""
    keep = ["run_id", "status", "years", "timings_seconds", "load", "analytic_hash",
            "validation", "rows_loaded", "rows_scanned", "error"]
    return {k: result[k] for k in keep if k in result}


# -------------------------------------------------------------------------- 1-3: runs
def evaluate_runs(cfg, refs):
    out = {}
    raw_dir = Path(cfg["paths"]["raw_dir"])

    # Cold run: no database, and no downloaded files.
    delete_db(cfg)
    for y in cfg["years"]:
        (raw_dir / f"{y}_annual_singlefile.zip").unlink(missing_ok=True)
    cold = run_pipeline(cfg, refs=None)
    out["cold_run"] = slim(cold)
    out["cold_run"]["rows_in_database"] = row_count(cfg["paths"]["db_path"])
    out["cold_run"]["database_size_mb"] = round(Path(cfg["paths"]["db_path"]).stat().st_size / 1e6, 2)

    # Repeat run on the same database with the files already downloaded.
    again = run_pipeline(cfg, refs=refs)
    out["repeat_run_same_database"] = slim(again)
    out["repeat_run_same_database"]["rows_in_database"] = row_count(cfg["paths"]["db_path"])
    out["repeat_run_same_database"]["same_data_as_cold_run"] = (
        again.get("analytic_hash") == cold.get("analytic_hash"))

    # Rebuild: throw the database away and load again from the downloaded files.
    delete_db(cfg)
    rebuilt = run_pipeline(cfg, refs=refs)
    out["rebuild_from_scratch"] = slim(rebuilt)
    out["rebuild_from_scratch"]["rows_in_database"] = row_count(cfg["paths"]["db_path"])
    out["rebuild_from_scratch"]["same_data_as_cold_run"] = (
        rebuilt.get("analytic_hash") == cold.get("analytic_hash"))
    return out


# -------------------------------------------------------------------------- 4a: faults
def load_standardized_frame(cfg):
    """Pull the loaded data back out of the database in the pipeline's own column layout."""
    conn = load.connect(cfg["paths"]["db_path"])
    try:
        df = pd.read_sql_query(
            f"SELECT {', '.join(transform.OUTPUT_COLUMNS)} FROM qcew_annual", conn)
    finally:
        conn.close()
    return df


def evaluate_fault_injection(cfg, refs):
    """Break a copy of the real data in specific ways and see which check notices."""
    base = load_standardized_frame(cfg)
    years = cfg["years"]
    county_total = base[(base.agglvl_code == "71") & (~base.area_fips.str.endswith("999"))
                        & base.employment.notna()]
    sector_row = base[(base.agglvl_code == "74") & base.employment.notna()].iloc[0]

    def pick(frame):
        return frame.index[0]

    faults = []

    def fault(name, expected_check, make):
        df = base.copy()
        df = make(df)
        results = validate.run_checks(df, refs, cfg, years)
        failed = [r.name for r in results if not r.passed and r.level != "info"]
        faults.append({
            "injected_fault": name,
            "expected_check": expected_check,
            "caught_by_expected_check": expected_check in failed,
            "all_checks_that_fired": failed,
        })

    def duplicate_row(df):
        return pd.concat([df, df.iloc[[0]]], ignore_index=True)

    def bad_area(df):
        df.loc[pick(df), "area_fips"] = "11777"
        return df

    def bad_industry(df):
        # My first version used "99999", but that's a real BLS code ("Unclassified"),
        # so the check was right to accept it. "ZZZZZ" can't be a real NAICS code.
        df.loc[pick(df), "industry_code"] = "ZZZZZ"
        return df

    def null_key(df):
        df.loc[pick(df), "industry_code"] = None
        return df

    def suppressed_zero(df):
        i = pick(df[df.is_suppressed == 1])
        df.loc[i, "employment"] = 0.0
        return df

    def negative(df):
        i = pick(df[df.employment.notna()])
        df.loc[i, "employment"] = -50.0
        return df

    def drop_year(df):
        return df[df.year != years[-1]]

    def drop_county_one_year(df):
        a = county_total.iloc[0]
        return df[~((df.area_fips == a.area_fips) & (df.year == a.year))]

    def inflate_county(df):
        i = county_total.index[0]
        df.loc[i, "employment"] = df.loc[i, "employment"] * 3
        return df

    def inflate_sector(df):
        i = sector_row.name
        df.loc[i, "employment"] = df.loc[i, "employment"] * 100 + 100000
        return df

    def wage_mismatch(df):
        i = pick(df[df.avg_annual_pay.notna()])
        df.loc[i, "avg_annual_pay"] = df.loc[i, "avg_annual_pay"] + 25000
        return df

    fault("Duplicate row", "no_duplicate_keys", duplicate_row)
    fault("Area code that doesn't exist", "valid_area_codes", bad_area)
    fault("Industry code that doesn't exist", "valid_industry_codes", bad_industry)
    fault("Missing key field", "required_fields_not_null", null_key)
    fault("Suppressed value stored as zero", "suppressed_values_are_null_not_zero", suppressed_zero)
    fault("Negative employment", "no_negative_measures", negative)
    fault("A whole year missing", "all_years_present_with_enough_rows", drop_year)
    fault("One county missing in one year", "same_counties_in_every_year", drop_county_one_year)
    fault("County total tripled", "county_totals_agree_with_state_total", inflate_county)
    fault("One sector inflated above county total", "sector_sum_not_above_county_total", inflate_sector)
    fault("Annual pay disagrees with weekly wage", "annual_pay_matches_weekly_wage", wage_mismatch)

    clean = validate.run_checks(base, refs, cfg, years)
    return {
        "clean_data_failed_checks": [r.name for r in clean if not r.passed and r.level != "info"],
        "faults": faults,
        "caught": sum(f["caught_by_expected_check"] for f in faults),
        "total": len(faults),
    }


# -------------------------------------------------------------------------- 4b: scenarios
def evaluate_failure_scenarios(cfg, refs):
    """Make real things go wrong and check the database is left untouched."""
    scenarios = []
    rows_before = row_count(cfg["paths"]["db_path"])
    hash_before = None
    conn = load.connect(cfg["paths"]["db_path"])
    hash_before = load.analytic_hash(conn)
    conn.close()

    def record(name, expected_status, result):
        conn = load.connect(cfg["paths"]["db_path"])
        hash_after = load.analytic_hash(conn)
        conn.close()
        scenarios.append({
            "scenario": name,
            "expected_status": expected_status,
            "actual_status": result["status"],
            "behaved_as_expected": result["status"] == expected_status,
            "database_rows_before": rows_before,
            "database_rows_after": row_count(cfg["paths"]["db_path"]),
            "data_unchanged": hash_after == hash_before,
            "message": result.get("error") or json.dumps(result.get("validation", {})),
        })

    # a) A year BLS hasn't published.
    c = copy.deepcopy(cfg)
    c["download"]["retries"] = 1
    record("Year that BLS hasn't published (2099)", "failed", run_pipeline(c, years=[2099], refs=refs))

    # b) A corrupted download sitting in the raw folder.
    c = copy.deepcopy(cfg)
    bad_dir = Path(cfg["paths"]["raw_dir"]) / "_corrupt_test"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "2020_annual_singlefile.zip").write_bytes(b"this is not a zip file")
    c["paths"]["raw_dir"] = bad_dir
    record("Corrupted zip file", "failed", run_pipeline(c, years=[2020], refs=refs))
    (bad_dir / "2020_annual_singlefile.zip").unlink()
    bad_dir.rmdir()

    # c) Validation fails end to end (I demand more rows per year than any year has).
    c = copy.deepcopy(cfg)
    c["validation"]["min_rows_per_year"] = 10_000_000
    record("Validation error blocks the load", "failed_validation", run_pipeline(c, refs=refs))

    return scenarios


# -------------------------------------------------------------------------- write-up
def write_markdown(res, path):
    cold, rep, reb = res["cold_run"], res["repeat_run_same_database"], res["rebuild_from_scratch"]
    f = res["fault_injection"]
    lines = [
        "# Pipeline evaluation results", "",
        f"Run on {res['environment']['date']} - {res['environment']['platform']}, "
        f"Python {res['environment']['python']}, pandas {res['environment']['pandas']}.", "",
        "## Runs", "",
        "| Scenario | Status | Seconds (total) | Rows inserted | Rows changed | Same data as cold run |",
        "|---|---|---|---|---|---|",
        f"| Cold run (downloads files) | {cold['status']} | {cold['timings_seconds'].get('total')} | "
        f"{cold['load']['inserted']:,} | {cold['load']['changed']:,} | - |",
        f"| Repeat run, same database | {rep['status']} | {rep['timings_seconds'].get('total')} | "
        f"{rep['load']['inserted']:,} | {rep['load']['changed']:,} | {rep['same_data_as_cold_run']} |",
        f"| Rebuild from scratch | {reb['status']} | {reb['timings_seconds'].get('total')} | "
        f"{reb['load']['inserted']:,} | {reb['load']['changed']:,} | {reb['same_data_as_cold_run']} |",
        "", f"Rows in database: {cold['rows_in_database']:,}. Database size: {cold['database_size_mb']} MB.", "",
        "## Faults I injected on purpose", "",
        f"{f['caught']} of {f['total']} injected faults were caught by the check built for them. "
        f"The unmodified data failed {len(f['clean_data_failed_checks'])} checks.", "",
        "| Injected fault | Expected check | Caught |", "|---|---|---|",
    ]
    for item in f["faults"]:
        lines.append(f"| {item['injected_fault']} | `{item['expected_check']}` | "
                     f"{'yes' if item['caught_by_expected_check'] else 'NO'} |")
    lines += ["", "## Things going wrong", "",
              "| Scenario | Expected | Actual | Data left unchanged |", "|---|---|---|---|"]
    for s in res["failure_scenarios"]:
        lines.append(f"| {s['scenario']} | {s['expected_status']} | {s['actual_status']} | {s['data_unchanged']} |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    cfg = load_config()
    cfg["years"] = resolve_years(cfg)       # the evaluation needs the actual list of years
    out_dir = Path(cfg["paths"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(out_dir, "evaluation_" + time.strftime("%Y%m%d_%H%M%S"))

    refs = collect.fetch_reference_tables(cfg)

    # --faults-only: redo just the fault-injection part against the database that's
    # already built, and keep the earlier run results. Handy when I fix a test.
    if "--faults-only" in sys.argv:
        results_file = out_dir / "evaluation_results.json"
        results = json.loads(results_file.read_text())
        results["fault_injection"] = evaluate_fault_injection(cfg, refs)
        results_file.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        write_markdown(results, out_dir / "evaluation_summary.md")
        print("Fault injection re-run. See", out_dir / "evaluation_summary.md")
        return

    results = {"environment": {
        "date": time.strftime("%Y-%m-%d"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pandas": pd.__version__,
    }}
    results.update(evaluate_runs(cfg, refs))
    results["fault_injection"] = evaluate_fault_injection(cfg, refs)
    results["failure_scenarios"] = evaluate_failure_scenarios(cfg, refs)

    (out_dir / "evaluation_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    write_markdown(results, out_dir / "evaluation_summary.md")
    print("\nDone. See", out_dir / "evaluation_summary.md")


if __name__ == "__main__":
    main()
