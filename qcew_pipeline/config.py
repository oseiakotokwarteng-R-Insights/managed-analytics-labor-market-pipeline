"""Reads config.toml and works out where files should live.

The default settings ship inside the package (qcew_pipeline/config.toml). Folders in the settings are
relative to the folder you run the program from, so `data/` and `outputs/` appear where you are working.
Pass --config to use your own settings file, or set the QCEW_* environment variables.

Which states to pull comes from a region (qcew_pipeline/regions.toml). Pick one with --region, with
QCEW_REGION, or with `region = "..."` in the settings. Anything the settings file sets itself wins.
"""

import os
import tomllib
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.toml"
REGIONS_FILE = Path(__file__).resolve().parent / "regions.toml"
DEFAULT_REGION = "dmv"


def load_regions():
    """Every ready-made region, by name."""
    with open(REGIONS_FILE, "rb") as f:
        return tomllib.load(f)


def add_region_argument(parser):
    """The --region option, the same on every command."""
    parser.add_argument(
        "--region",
        help="which group of states to use, for example dmv or california (default: the region in the settings)",
    )


def load_config(path=None, region=None):
    """Load the settings file and turn the folder paths into real absolute paths.

    Environment variables win over the file, which is handy for GitHub Actions
    and for keeping the big raw downloads out of a synced folder.

    The region (which states, where the files go) is filled in last, and only for what the settings
    file doesn't already say. The region comes from the `region` argument, then QCEW_REGION, then the
    settings file, then the default.
    """
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    _apply_region(cfg, region or os.environ.get("QCEW_REGION") or cfg.get("region") or DEFAULT_REGION)
    paths = cfg["paths"]
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


def _apply_region(cfg, name):
    """Fill in the states, county counts and file names from the region, unless the settings already have them."""
    regions = load_regions()
    if name not in regions:
        raise ValueError(f"Unknown region '{name}'. The choices are: {', '.join(sorted(regions))}.")
    preset = regions[name]
    cfg["region"] = name
    cfg["region_name"] = preset["name"]
    cfg.setdefault("state_fips", preset["state_fips"])
    cfg.setdefault("validation", {}).setdefault("expected_county_counts", preset["expected_county_counts"])
    for key in ("db_path", "lake_dir", "analytics_db"):
        cfg["paths"].setdefault(key, preset[key])


def _check_years(cfg):
    """Fail early, with a plain message, if the years setting is something the pipeline can't use."""
    years = cfg.get("years")
    if years == "auto":
        window = cfg.get("window_years")
        if not isinstance(window, int) or isinstance(window, bool) or window < 1:
            raise ValueError('window_years must be a whole number of 1 or more when years = "auto".')
    elif not (isinstance(years, list) and years and all(isinstance(y, int) for y in years)):
        raise ValueError('years must be "auto" or a list of years such as [2022, 2023, 2024].')
