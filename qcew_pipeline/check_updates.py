"""Checks whether BLS has published a newer annual QCEW file than the ones in config.toml.

    python -m qcew_pipeline.check_updates

It only sends a HEAD request (no download), so it is cheap. The exit code matters:

    0  the check worked (whether or not there is new data)
    2  the check could not reach BLS, so a broken check can't pass for "no news"

When it runs in GitHub Actions it also writes `new_data` and `year` to the step outputs,
and the workflow uses them to open an issue.
"""

import os
import sys
import urllib.error
import urllib.request

from .config import load_config


class CheckError(Exception):
    """Raised when BLS can't be reached or gives an answer the check doesn't understand."""


def year_exists(year, cfg):
    """True if BLS has published the annual file for this year, False if it has not (404)."""
    url = cfg["urls"]["annual_file"].format(year=year)
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": cfg["download"]["user_agent"]})
    try:
        with urllib.request.urlopen(req, timeout=cfg["download"]["timeout_seconds"]) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return False
        raise CheckError(f"BLS answered {err.code} for {url}") from err
    except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
        raise CheckError(f"Could not reach BLS ({url}): {err}") from err


def check(cfg):
    """Look for the year after the latest one in the config."""
    latest = max(cfg["years"])
    candidate = latest + 1
    return {"latest_configured": latest, "candidate": candidate,
            "new_data": year_exists(candidate, cfg)}


def describe(result):
    if result["new_data"]:
        return (f"NEW DATA: BLS has published the {result['candidate']} annual file. "
                f"Add {result['candidate']} to `years` in config.toml and re-run the pipeline.")
    return (f"No new annual file yet. Latest configured year is {result['latest_configured']}; "
            f"{result['candidate']} is not published.")


def write_github_output(result):
    """Pass the answer to later steps of a GitHub Actions job (does nothing anywhere else)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"new_data={'true' if result['new_data'] else 'false'}\n")
        f.write(f"year={result['candidate']}\n")


def main():
    cfg = load_config()
    try:
        result = check(cfg)
    except CheckError as err:
        print(f"CHECK FAILED: {err}", file=sys.stderr)
        return 2
    print(describe(result))
    write_github_output(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
