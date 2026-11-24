"""Asks BLS which annual QCEW file is the newest, and says whether there is news.

    python -m qcew_pipeline.check_updates

It only sends header requests (no download), so it is cheap. The exit code matters:

    0  the check worked (whether or not there is news)
    2  the check could not reach BLS, so a broken check can't pass for "no news"

With years = "auto" in config.toml the pipeline follows the newest year by itself, so the check
just reports the newest year and the window the next run will use. With a fixed list of years,
"news" means BLS has published a year after the last one in the list.

When it runs in GitHub Actions it also writes `new_data`, `year` and `window` to the step outputs,
and the workflow uses them to open an issue.
"""

import os
import sys

from . import years
from .config import load_config
from .years import CheckError

__all__ = ["CheckError", "check", "describe", "write_github_output", "main"]


def check(cfg):
    """Find the newest published year and compare it with what the config would pull."""
    latest = years.latest_published_year(cfg)
    if years.is_auto(cfg):
        window = years.window_ending_at(latest, cfg["window_years"])
        return {"mode": "auto", "latest_published": latest, "window": window, "new_data": True}
    window = years.resolve_years(cfg)
    return {"mode": "pinned", "latest_published": latest, "window": window,
            "new_data": latest > max(window)}


def _span(window):
    return f"{window[0]}-{window[-1]}"


def describe(result):
    if result["mode"] == "auto":
        return (f"Newest BLS annual file: {result['latest_published']}. The pipeline follows the newest year "
                f"automatically, so the next run will pull {_span(result['window'])}.")
    if result["new_data"]:
        return (f"NEW DATA: BLS has published the {result['latest_published']} annual file. "
                f"Add it to `years` in config.toml (or set years = \"auto\") and re-run the pipeline.")
    return (f"No new annual file yet. Latest configured year is {max(result['window'])}; "
            f"newest published is {result['latest_published']}.")


def write_github_output(result):
    """Pass the answer to later steps of a GitHub Actions job (does nothing anywhere else)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"new_data={'true' if result['new_data'] else 'false'}\n")
        f.write(f"year={result['latest_published']}\n")
        f.write(f"window={_span(result['window'])}\n")


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
