"""Works out which years to pull.

config.toml says either a fixed list (years = [2022, 2023, 2024]) or "auto". With "auto" the
pipeline asks BLS which annual file is the newest one and uses the most recent `window_years`
years up to and including it. When BLS publishes a new year, the window moves forward by itself.
Years that fall out of the window stay in the database; they just stop being refreshed.
"""

import urllib.error
import urllib.request
from datetime import date

# How many calendar years back to look for the newest published file.
LOOKBACK_YEARS = 4


class CheckError(Exception):
    """Raised when BLS can't be reached or gives an answer that doesn't make sense."""


def year_exists(year, cfg, url_key="annual_file"):
    """True if the file for this year is published, False if it is not (404). Asks BLS unless told another source."""
    url = cfg["urls"][url_key].format(year=year, yy=f"{year % 100:02d}")
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": cfg["download"]["user_agent"]})
    try:
        with urllib.request.urlopen(req, timeout=cfg["download"]["timeout_seconds"]) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return False
        raise CheckError(f"{url} answered {err.code}") from err
    except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
        raise CheckError(f"Could not reach {url}: {err}") from err


def latest_published_year(cfg, today=None):
    """The newest year BLS has an annual file for. Looks from this year backwards."""
    this_year = (today or date.today()).year
    for year in range(this_year, this_year - LOOKBACK_YEARS, -1):
        if year_exists(year, cfg):
            return year
    raise CheckError(f"No annual QCEW file found for {this_year - LOOKBACK_YEARS + 1}-{this_year}.")


def is_auto(cfg):
    return cfg["years"] == "auto"


def window_ending_at(latest, window_years):
    return list(range(latest - window_years + 1, latest + 1))


def resolve_years(cfg, today=None):
    """The list of years this run should pull."""
    if not is_auto(cfg):
        return sorted(int(y) for y in cfg["years"])
    latest = latest_published_year(cfg, today)
    return window_ending_at(latest, cfg["window_years"])
