"""Step 7: build the analytics layer with dbt.

dbt reads the Parquet files that qcew-publish wrote, cleans them up in a set of staging models, builds
summary tables from those (jobs and pay by county and year, by county and sector, and by state), and runs
its tests on all of it. The result is a small DuckDB file you can open with any DuckDB tool.

    python -m qcew_pipeline.analytics
    python -m qcew_pipeline.analytics --select mart_county_year

The dbt project is the analytics folder in the repository. This command only points dbt at the right
files and passes in the limits from config.toml, so there is one place to change them. It needs the
analytics extras (pip install -e ".[analytics]") and a publish first (qcew-run --publish).
"""

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .config import load_config


class AnalyticsError(Exception):
    """Raised when the analytics build can't even start (no published files, no dbt project)."""


def find_project():
    """The dbt project folder: QCEW_ANALYTICS_DIR if set, else ./analytics, else the one next to the package."""
    from_env = os.environ.get("QCEW_ANALYTICS_DIR")
    if from_env:
        candidates = [Path(from_env)]
    else:
        candidates = [Path.cwd() / "analytics", Path(__file__).resolve().parent.parent / "analytics"]
    for folder in candidates:
        if (folder / "dbt_project.yml").is_file():
            return folder
    raise AnalyticsError(
        "Could not find the dbt project (the analytics folder). Run this from the repository folder, "
        "or set QCEW_ANALYTICS_DIR to its location."
    )


@contextmanager
def _environment(values):
    """Set environment variables for the length of a block, then put them back."""
    before = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old in before.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def build_analytics(cfg, project_dir=None, select=None):
    """Run dbt build (seeds, models and tests). Returns True only if every model built and every test passed."""
    lake = Path(cfg["paths"]["lake_dir"])
    if not (lake / "manifest.json").is_file():
        raise AnalyticsError(f"No published files found in {lake}. Publish first with: qcew-run --publish")
    project = Path(project_dir) if project_dir else find_project()

    from dbt.cli.main import dbtRunner  # imported here so the rest of the pipeline works without dbt

    out = Path(cfg["paths"]["output_dir"]) / "dbt"
    db = Path(cfg["paths"]["analytics_db"])
    db.parent.mkdir(parents=True, exist_ok=True)
    limits = {
        "state_gap_tolerance": cfg["validation"]["state_reconciliation_tolerance"],
        "sector_sum_tolerance": cfg["validation"]["sector_sum_tolerance"],
    }
    args = [
        "build",
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(project),
        "--target-path",
        str(out / "target"),
        "--log-path",
        str(out / "logs"),
        "--vars",
        json.dumps(limits),
    ]
    if select:
        args += ["--select", select]

    environment = {
        "QCEW_LAKE_DIR": lake.as_posix(),
        "QCEW_ANALYTICS_DB": db.as_posix(),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "False",  # don't send usage statistics anywhere
    }
    with _environment(environment):
        result = dbtRunner().invoke(args)
    if result.exception is not None:
        raise AnalyticsError(f"dbt could not run: {result.exception}")
    return bool(result.success)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the analytics layer (dbt models and tests) from the Parquet files."
    )
    parser.add_argument("--config", help="path to a config.toml (default: the one inside the package)")
    parser.add_argument("--select", help="build only these models or tests, using dbt's selection syntax")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    try:
        passed = build_analytics(cfg, select=args.select)
    except ModuleNotFoundError as err:
        print(
            f'{err.name} isn\'t installed. Install the analytics extras with: pip install -e ".[analytics]"',
            file=sys.stderr,
        )
        return 2
    except AnalyticsError as err:
        print(err, file=sys.stderr)
        return 2
    if not passed:
        print("The analytics build had failures. The dbt output above says which.", file=sys.stderr)
        return 1
    print(f"Analytics layer built: {cfg['paths']['analytics_db']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
