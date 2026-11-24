"""Checks whether BLS has published a newer annual QCEW file than the ones in my config.

I run this on a schedule in GitHub Actions. It only looks (a HEAD request, no
download), so it's cheap. If a newer year exists it says so and exits with code 0
either way; the message is what matters.
"""

import sys
import urllib.error
import urllib.request

from .config import load_config


def year_exists(year, cfg):
    url = cfg["urls"]["annual_file"].format(year=year)
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": cfg["download"]["user_agent"]})
    try:
        with urllib.request.urlopen(req, timeout=cfg["download"]["timeout_seconds"]) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return False
        raise


def main():
    cfg = load_config()
    latest_configured = max(cfg["years"])
    candidate = latest_configured + 1
    if year_exists(candidate, cfg):
        print(f"NEW DATA: BLS has published the {candidate} annual file. "
              f"Add {candidate} to `years` in config.toml and re-run the pipeline.")
    else:
        print(f"No new annual file yet. Latest configured year is {latest_configured}; "
              f"{candidate} is not published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
