"""Runs the whole pipeline from start to finish.

    python -m qcew_pipeline.run                 # all years in config.toml
    python -m qcew_pipeline.run --years 2023 2024
    python -m qcew_pipeline.run --export        # also write the CSV exports

The order is: collect -> transform -> validate -> load. If validation finds a
real error, nothing gets loaded and the run is marked as failed so the
database never ends up with questionable data in it.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

from . import collect, load, transform, validate
from .config import load_config
from .years import CheckError, resolve_years

log = logging.getLogger("qcew")


def setup_logging(output_dir, run_label):
    """Log to the screen and to a file in outputs/logs/."""
    log_dir = Path(output_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"run_{run_label}.log"
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    logger = logging.getLogger("qcew")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return log_file


def run_pipeline(cfg, years=None, refs=None):
    """Run every step. Returns a dict of results and timings (and never raises for
    expected problems like a failed download; those show up as status = 'failed').
    """
    years = list(years) if years else resolve_years(cfg)
    timings: dict[str, float] = {}
    result = {"years": years, "status": "failed", "timings_seconds": timings}
    t_total = time.perf_counter()

    conn = load.connect(cfg["paths"]["db_path"])
    load.init_schema(conn)
    run_id = load.start_run(conn, years)
    result["run_id"] = run_id
    log.info("=== run %d started for years %s ===", run_id, years)

    try:
        # ---- 1. collect ----
        t = time.perf_counter()
        refs = refs or collect.fetch_reference_tables(cfg)
        files = [collect.download_annual_file(y, cfg) for y in years]
        timings["collect"] = round(time.perf_counter() - t, 2)

        # ---- 2. transform ----
        t = time.perf_counter()
        frames, stats_by_year = [], {}
        for info in files:
            raw, stats = transform.read_year(info["local_path"], cfg)
            stats_by_year[info["year"]] = stats
            frames.append(transform.standardize(raw))
        data = pd.concat(frames, ignore_index=True)
        timings["transform"] = round(time.perf_counter() - t, 2)

        # ---- 3. validate ----
        t = time.perf_counter()
        rows_by_year = {y: s["rows_kept"] for y, s in stats_by_year.items()}
        checks = validate.run_checks(data, refs, cfg, years, rows_by_year)
        summary = validate.summarize(checks)
        timings["validate"] = round(time.perf_counter() - t, 2)
        for c in checks:
            mark = "PASS" if c.passed else ("FAIL" if c.level == "error" else "WARN")
            log.info("check %-4s %-42s %s", mark, c.name, c.detail)

        # Save provenance and check results whether or not we go on to load.
        source_ids = {}
        for info in files:
            s = stats_by_year[info["year"]]
            source_ids[info["year"]] = load.record_source_file(
                conn, run_id, info, s["rows_scanned"], s["rows_kept"])
        load.record_validation(conn, run_id, checks)
        scanned = sum(s["rows_scanned"] for s in stats_by_year.values())

        if summary["errors"] > 0:
            log.error("Validation found %d error(s). Nothing was loaded.", summary["errors"])
            load.finish_run(conn, run_id, "failed_validation", rows_scanned=scanned,
                            rows_kept=len(data), validation_errors=summary["errors"],
                            validation_warnings=summary["warnings"],
                            notes="Validation errors; data not loaded.")
            result.update(status="failed_validation", validation=summary,
                          checks=[c.as_dict() for c in checks])
            return result

        # ---- 4. load ----
        t = time.perf_counter()
        load.load_reference_tables(conn, refs, cfg)
        counts = load.load_facts(conn, data, run_id, source_ids, cfg)
        digest = load.analytic_hash(conn)
        timings["load"] = round(time.perf_counter() - t, 2)

        # Post-load sanity check: every row I meant to load should now be in the table.
        in_db = load.count_in_scope(conn, years, cfg)
        if in_db != len(data):
            raise RuntimeError(f"Post-load check failed: expected {len(data)} rows, found {in_db}")

        timings["total"] = round(time.perf_counter() - t_total, 2)
        load.finish_run(conn, run_id, "success", rows_scanned=scanned, rows_kept=len(data),
                        rows_inserted=counts["inserted"], rows_changed=counts["changed"],
                        rows_unchanged=counts["unchanged"], rows_removed=counts["removed"],
                        validation_errors=summary["errors"],
                        validation_warnings=summary["warnings"], analytic_hash=digest)
        log.info("=== run %d finished: %s inserted, %s changed, %s unchanged, %s removed (%.1fs) ===",
                 run_id, f"{counts['inserted']:,}", f"{counts['changed']:,}",
                 f"{counts['unchanged']:,}", f"{counts['removed']:,}", timings["total"])
        if counts["removed"]:
            log.warning("%s stored rows are no longer published by BLS. They were moved to "
                        "qcew_annual_removed.", f"{counts['removed']:,}")
        result.update(status="success", load=counts, analytic_hash=digest, validation=summary,
                      rows_loaded=len(data), rows_scanned=scanned,
                      checks=[c.as_dict() for c in checks])
        return result

    except load.TooManyRemovedError as err:
        log.error("%s", err)
        load.finish_run(conn, run_id, "failed_validation", notes=str(err))
        result.update(status="failed_validation", error=str(err))
        return result
    except collect.DownloadError as err:
        log.error("Download problem: %s", err)
        load.finish_run(conn, run_id, "failed", notes=f"Download problem: {err}")
        result["error"] = str(err)
        return result
    except Exception as err:  # anything unexpected still gets recorded
        log.exception("Unexpected failure")
        load.finish_run(conn, run_id, "failed", notes=f"{type(err).__name__}: {err}")
        result["error"] = f"{type(err).__name__}: {err}"
        return result
    finally:
        timings.setdefault("total", round(time.perf_counter() - t_total, 2))
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the QCEW DMV data pipeline.")
    parser.add_argument("--config", help="path to a config.toml (default: the one inside the package)")
    parser.add_argument("--years", nargs="+", type=int, help="override the years in the config")
    parser.add_argument("--export", action="store_true", help="write CSV exports after a successful run")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_file = setup_logging(cfg["paths"]["output_dir"], stamp)
    try:
        years = args.years or resolve_years(cfg)
    except CheckError as err:
        log.error("Could not work out which years to pull: %s", err)
        return 2
    log.info("years for this run: %s-%s", years[0], years[-1])
    result = run_pipeline(cfg, years)

    report = Path(cfg["paths"]["output_dir"]) / f"run_summary_{stamp}.json"
    report.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    log.info("log: %s", log_file)
    log.info("summary: %s", report)

    if result["status"] == "success" and args.export:
        from .export import export_all
        export_all(cfg)

    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
