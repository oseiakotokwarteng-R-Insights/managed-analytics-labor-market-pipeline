"""Reads config.toml and works out where files should live."""

import os
import tomllib
from pathlib import Path

# The project folder is one level up from this file.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.toml"


def load_config(path=None):
    """Load the settings file and turn the folder paths into real absolute paths.

    Environment variables win over the file, which is handy for GitHub Actions
    and for keeping the big raw downloads out of a synced folder.
    """
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    paths = cfg["paths"]
    env_overrides = {
        "raw_dir": os.environ.get("QCEW_RAW_DIR"),
        "db_path": os.environ.get("QCEW_DB_PATH"),
        "output_dir": os.environ.get("QCEW_OUTPUT_DIR"),
    }
    for key, env_value in env_overrides.items():
        value = env_value or paths[key]
        p = Path(value)
        paths[key] = p if p.is_absolute() else PROJECT_ROOT / p

    # Allow the user agent to come from the environment so my contact details
    # don't have to live in a file that gets pushed to GitHub.
    if os.environ.get("QCEW_USER_AGENT"):
        cfg["download"]["user_agent"] = os.environ["QCEW_USER_AGENT"]

    return cfg
