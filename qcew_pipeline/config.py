"""Reads config.toml and works out where files should live.

The default settings ship inside the package (qcew_pipeline/config.toml). Folders in the settings are
relative to the folder you run the program from, so `data/` and `outputs/` appear where you are working.
Pass --config to use your own settings file, or set the QCEW_* environment variables.
"""

import os
import tomllib
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.toml"


def load_config(path=None):
    """Load the settings file and turn the folder paths into real absolute paths.

    Environment variables win over the file, which is handy for GitHub Actions
    and for keeping the big raw downloads out of a synced folder.
    """
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    paths = cfg["paths"]
    paths.setdefault("lake_dir", "data/lake")      # settings files written before these existed still work
    paths.setdefault("analytics_db", "data/analytics.duckdb")
    env_overrides = {
        "raw_dir": os.environ.get("QCEW_RAW_DIR"),
        "db_path": os.environ.get("QCEW_DB_PATH"),
        "output_dir": os.environ.get("QCEW_OUTPUT_DIR"),
        "lake_dir": os.environ.get("QCEW_LAKE_DIR"),
        "analytics_db": os.environ.get("QCEW_ANALYTICS_DB"),
    }
    for key, env_value in env_overrides.items():
        value = env_value or paths[key]
        p = Path(value)
        paths[key] = p if p.is_absolute() else Path.cwd() / p

    # Allow the user agent to come from the environment so my contact details
    # don't have to live in a file that gets pushed to GitHub.
    if os.environ.get("QCEW_USER_AGENT"):
        cfg["download"]["user_agent"] = os.environ["QCEW_USER_AGENT"]

    _check_years(cfg)
    return cfg


def _check_years(cfg):
    """Fail early, with a plain message, if the years setting is something the pipeline can't use."""
    years = cfg.get("years")
    if years == "auto":
        window = cfg.get("window_years")
        if not isinstance(window, int) or isinstance(window, bool) or window < 1:
            raise ValueError('window_years must be a whole number of 1 or more when years = "auto".')
    elif not (isinstance(years, list) and years and all(isinstance(y, int) for y in years)):
        raise ValueError('years must be "auto" or a list of years such as [2022, 2023, 2024].')
