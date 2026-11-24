"""Step 1: go get the files from BLS and keep a record of what we got.

Nothing in here changes the data. It just downloads it, fingerprints it
(so I can prove later which file a number came from), and fetches the
BLS code/title files used to translate codes into names.
"""

import hashlib
import io
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger("qcew")


class DownloadError(Exception):
    """Raised when a file can't be downloaded after all the retries."""


def _open(url, cfg):
    req = urllib.request.Request(url, headers={"User-Agent": cfg["download"]["user_agent"]})
    return urllib.request.urlopen(req, timeout=cfg["download"]["timeout_seconds"])


def sha256_of_file(path, block=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def download_annual_file(year, cfg):
    """Download one year's annual QCEW zip (or reuse it if it's already here).

    Returns a dict describing the file: url, where it is, size, hash, status.
    Raises DownloadError if BLS can't be reached or the file doesn't exist.
    """
    url = cfg["urls"]["annual_file"].format(year=year)
    raw_dir = Path(cfg["paths"]["raw_dir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / f"{year}_annual_singlefile.zip"

    if dest.exists() and cfg["download"]["reuse_existing_files"]:
        log.info("%s: already on disk, reusing %s", year, dest.name)
        status = "reused_existing"
    else:
        status = _download_with_retries(url, dest, cfg, year)

    return {
        "year": year,
        "url": url,
        "local_path": str(dest),
        "local_name": dest.name,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "download_status": status,
        "size_bytes": dest.stat().st_size,
        "sha256": sha256_of_file(dest),
    }


def _download_with_retries(url, dest, cfg, year):
    retries = cfg["download"]["retries"]
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            log.info("%s: downloading (attempt %d of %d) %s", year, attempt, retries, url)
            part = dest.with_suffix(".part")
            with _open(url, cfg) as resp, open(part, "wb") as out:
                while chunk := resp.read(1024 * 1024):
                    out.write(chunk)
            part.replace(dest)  # only rename once the whole file made it
            return "downloaded"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last_error = err
            # A 404 means the file really isn't there, so retrying won't help.
            if isinstance(err, urllib.error.HTTPError) and err.code == 404:
                break
            log.warning("%s: attempt %d failed: %s", year, attempt, err)
            time.sleep(2 * attempt)
    raise DownloadError(f"Could not download {url}: {last_error}")


def fetch_reference_table(name, cfg):
    """Download one of the small BLS code/title CSV files into a DataFrame."""
    url = cfg["urls"][name]
    try:
        with _open(url, cfg) as resp:
            text = resp.read().decode("utf-8-sig")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
        raise DownloadError(f"Could not download reference file {url}: {err}") from err
    # Read everything as text so codes like "31-33" and leading zeros stay intact.
    return pd.read_csv(io.StringIO(text), dtype=str)


def fetch_reference_tables(cfg):
    """Get the four code/title lookups: areas, industries, ownerships, aggregation levels."""
    tables = {
        "area": fetch_reference_table("area_titles", cfg),
        "industry": fetch_reference_table("industry_titles", cfg),
        "ownership": fetch_reference_table("ownership_titles", cfg),
        "agglevel": fetch_reference_table("agglevel_titles", cfg),
    }
    for name, df in tables.items():
        log.info("reference table %s: %d rows", name, len(df))
    return tables
