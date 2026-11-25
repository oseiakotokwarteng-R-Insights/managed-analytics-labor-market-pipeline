"""Step 8: run the pipeline on a schedule with Dagster, and show how the data flows.

Dagster doesn't replace anything here. Each asset below calls the same code you can run by hand
(qcew-run, qcew-publish, qcew-build), so the command line and Dagster always do the same thing.

    qcew_database  ->  qcew_parquet_files  ->  qcew_analytics_tables
         |
         +-->  cbp_comparison

  qcew_database          collect, transform, check and load into SQLite (what qcew-run does)
  qcew_parquet_files     the Parquet files (what qcew-publish does)
  qcew_analytics_tables  the dbt tables and their tests (what qcew-build does)
  cbp_comparison         the second source, Census County Business Patterns, compared with QCEW (qcew-cbp)

Three checks are attached. One warns if the validation step raised any warnings (the drift ones, for
example). One runs the cross-check, which asks the database, pandas and dbt the same questions and
compares the answers. One warns if the two sources, QCEW and County Business Patterns, disagree.

There is one job that refreshes everything, a monthly schedule for it, and a sensor that asks BLS once
a day whether it has published a year the database doesn't have yet. The schedule and the sensor start
switched off, so nothing downloads anything until you turn them on in the Dagster screen.

    pip install -e ".[orchestration]"
    dagster dev

Set DAGSTER_HOME to a folder first if you want Dagster to remember its run history between sessions.
Use QCEW_CONFIG to point at your own settings file.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import dagster as dg

from . import years
from .analytics import AnalyticsError, build_analytics
from .cbp import CbpError, run_cbp
from .collect import DownloadError
from .config import load_config
from .load import connect
from .publish import publish_lake
from .run import run_pipeline, setup_logging

GROUP = "qcew"
CROSS_CHECK_SCRIPT = Path(__file__).resolve().parent.parent / "evaluation" / "cross_check_analytics.py"


def _config():
    return load_config(os.environ.get("QCEW_CONFIG") or None)


def newest_loaded_year(cfg):
    """The newest year in the database, or None if there is no database or nothing in it yet."""
    db = Path(cfg["paths"]["db_path"])
    if not db.is_file():
        return None
    conn = connect(db)
    try:
        row = conn.execute("SELECT MAX(year) FROM qcew_annual").fetchone()
    except Exception:  # the file exists but the tables don't (an empty or half-made database)
        return None
    finally:
        conn.close()
    return None if row is None or row[0] is None else int(row[0])


@dg.asset(
    group_name=GROUP,
    kinds={"python", "sqlite"},
    description="BLS annual files collected, checked and loaded into the SQLite database.",
    check_specs=[dg.AssetCheckSpec("no_validation_warnings", asset="qcew_database")],
)
def qcew_database():
    cfg = _config()
    setup_logging(cfg["paths"]["output_dir"], time.strftime("%Y%m%d_%H%M%S"))
    try:
        result = run_pipeline(cfg)
    except years.CheckError as err:
        raise dg.Failure(description=f"Could not work out which years to pull: {err}") from err

    if result["status"] != "success":
        failed = [c["name"] for c in result.get("checks", []) if c["level"] == "error" and not c["passed"]]
        raise dg.Failure(
            description=result.get("error") or f"The run ended as {result['status']}. Nothing was loaded.",
            metadata={
                "run_id": result.get("run_id"),
                "status": result["status"],
                "failed_checks": ", ".join(failed) or "none",
            },
        )

    warnings = [c for c in result["checks"] if c["level"] == "warn" and not c["passed"]]
    counts = result["load"]
    yield dg.MaterializeResult(
        metadata={
            "run_id": result["run_id"],
            "years": f"{result['years'][0]}-{result['years'][-1]}",
            "rows_loaded": result["rows_loaded"],
            "rows_inserted": counts["inserted"],
            "rows_changed": counts["changed"],
            "rows_unchanged": counts["unchanged"],
            "rows_removed": counts["removed"],
            "analytic_hash": result["analytic_hash"],
            "timings_seconds": dg.MetadataValue.json(result["timings_seconds"]),
        }
    )
    yield dg.AssetCheckResult(
        check_name="no_validation_warnings",
        passed=not warnings,
        severity=dg.AssetCheckSeverity.WARN,
        description="The validation step had no warnings." if not warnings else f"{len(warnings)} warning(s).",
        metadata={"warnings": "\n".join(f"{c['name']}: {c['detail']}" for c in warnings) or "none"},
    )


@dg.asset(
    group_name=GROUP,
    deps=[qcew_database],
    kinds={"python", "parquet"},
    description="The database written out as Parquet files, one folder per year, with a manifest.",
)
def qcew_parquet_files():
    cfg = _config()
    try:
        manifest = publish_lake(cfg)
    except (ModuleNotFoundError, ValueError) as err:
        raise dg.Failure(description=f"Could not publish the Parquet files: {err}") from err
    table = manifest["tables"]["qcew_annual"]
    published = sorted(table["partitions"])
    return dg.MaterializeResult(
        metadata={
            "rows": table["rows"],
            "years": f"{published[0]}-{published[-1]}",
            "analytic_hash": manifest["analytic_hash"],
            "folder": str(cfg["paths"]["lake_dir"]),
        }
    )


@dg.asset(
    group_name=GROUP,
    deps=[qcew_parquet_files],
    kinds={"dbt", "duckdb"},
    description="The dbt summary tables built from the Parquet files, with all of dbt's tests.",
)
def qcew_analytics_tables():
    cfg = _config()
    try:
        passed = build_analytics(cfg)
    except (AnalyticsError, ModuleNotFoundError) as err:
        raise dg.Failure(description=f"Could not build the analytics tables: {err}") from err
    if not passed:
        raise dg.Failure(description="The dbt build had failures. The step's log shows which models or tests.")
    return dg.MaterializeResult(metadata={"database": str(cfg["paths"]["analytics_db"])})


@dg.asset(
    group_name=GROUP,
    deps=[qcew_database],
    kinds={"python", "sqlite"},
    description="The second source, Census County Business Patterns, downloaded and compared with QCEW.",
    check_specs=[dg.AssetCheckSpec("sources_agree", asset="cbp_comparison")],
)
def cbp_comparison():
    cfg = _config()
    try:
        result = run_cbp(cfg)
    except CbpError as err:
        raise dg.Failure(description=str(err)) from err
    except (DownloadError, years.CheckError, ValueError) as err:
        raise dg.Failure(description=f"Could not compare with County Business Patterns: {err}") from err

    warnings = [r for r in result["results"] if r["level"] == "warn" and not r["passed"]]
    yield dg.MaterializeResult(
        metadata={
            "years_compared": f"{result['loaded_years'][0]}-{result['loaded_years'][-1]}",
            "years_not_published_yet": ", ".join(str(y) for y in result["not_published"]) or "none",
            "rows_saved": dg.MetadataValue.json({str(y): n for y, n in result["rows_saved"].items()}),
            "report": result["report"],
        }
    )
    yield dg.AssetCheckResult(
        check_name="sources_agree",
        passed=not warnings,
        severity=dg.AssetCheckSeverity.WARN,
        description="QCEW and County Business Patterns are close." if not warnings else f"{len(warnings)} warning(s).",
        metadata={"warnings": "\n".join(f"{r['check_name']}: {r['detail']}" for r in warnings) or "none"},
    )


@dg.asset_check(
    asset=qcew_analytics_tables,
    description="The database, pandas and dbt give the same answers, row by row.",
)
def database_pandas_dbt_agree():
    if not CROSS_CHECK_SCRIPT.is_file():
        return dg.AssetCheckResult(
            passed=False,
            description="The cross-check script wasn't found. Run Dagster from a checkout of the repository.",
        )
    command = [sys.executable, str(CROSS_CHECK_SCRIPT)]
    if os.environ.get("QCEW_CONFIG"):
        command += ["--config", os.environ["QCEW_CONFIG"]]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    output = (done.stdout + done.stderr).strip()
    meanings = {0: "Everything matches.", 1: "Something doesn't match."}
    meaning = meanings.get(done.returncode, "The check couldn't run.")
    return dg.AssetCheckResult(
        passed=done.returncode == 0,
        description=meaning,
        metadata={"exit_code": done.returncode, "output": output[-2000:]},
    )


qcew_refresh = dg.define_asset_job(
    "qcew_refresh",
    selection=dg.AssetSelection.groups(GROUP),
    description="Collect, check, load, publish and build everything, then run the cross-check.",
)

monthly_refresh = dg.ScheduleDefinition(
    name="qcew_monthly_refresh",
    job=qcew_refresh,
    cron_schedule="0 14 1 * *",  # 14:00 UTC on the first of the month, the same time the GitHub check runs
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
    description="Refreshes everything once a month.",
)


@dg.sensor(
    job=qcew_refresh,
    minimum_interval_seconds=24 * 60 * 60,
    default_status=dg.DefaultSensorStatus.STOPPED,
    description="Starts a refresh when BLS has published a year the database doesn't have yet.",
)
def new_bls_year_sensor(context: dg.SensorEvaluationContext):
    cfg = _config()
    # If BLS can't be reached this raises, and Dagster shows the tick as failed. A broken check
    # should never look like "no news".
    latest = years.latest_published_year(cfg)
    loaded = newest_loaded_year(cfg)
    if loaded is not None and latest <= loaded:
        return dg.SkipReason(f"BLS's newest file is {latest}, and the database already has {loaded}.")
    # One run per new year, even if the sensor ticks again before that run finishes.
    return dg.RunRequest(run_key=f"qcew-{latest}", tags={"newest_published_year": str(latest)})


defs = dg.Definitions(
    assets=[qcew_database, qcew_parquet_files, qcew_analytics_tables, cbp_comparison],
    asset_checks=[database_pandas_dbt_agree],
    jobs=[qcew_refresh],
    schedules=[monthly_refresh],
    sensors=[new_bls_year_sensor],
)
