"""Step 9: a second source. The Census Bureau's County Business Patterns (CBP), compared with QCEW.

QCEW counts jobs from the unemployment insurance records that employers file. CBP counts them from a
different place: Census business records and surveys. They describe much of the same economy, so if my
QCEW numbers are right, CBP should land close to them. When it doesn't, that's worth knowing.

    qcew-cbp                      # for every year in the database that CBP has published
    qcew-cbp --years 2022 2023

What it does, in order:

  1. Works out which years to compare: the years already in the database that CBP has published.
     CBP comes out later than QCEW, so the newest QCEW years usually have no CBP file yet. That's fine.
  2. Downloads each county file from Census (about 12 MB a year) and keeps a fingerprint of it.
  3. Keeps the county rows for the states in the region, for all industries and for each two-digit sector.
  4. Saves them in their own tables (cbp_county and cbp_files), apart from the BLS data.
  5. Compares the two sources and writes the results (see cross_source.py).

It needs the QCEW data loaded first (qcew-run). It never changes the QCEW tables.

Withheld values stay empty, the same rule as everywhere else here. Census hides some numbers to protect
businesses, and since 2020 it also adds a little random noise to the ones it shows. The noise flag
(G, H or J) says how much, and I keep it next to the number.
"""

import argparse
import logging
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import collect, cross_source, load, years
from .config import add_region_argument, load_config

log = logging.getLogger("qcew")

REQUIRED_COLUMNS = ["FIPSTATE", "FIPSCTY", "NAICS", "EMP", "AP", "EST"]
OPTIONAL_COLUMNS = ["EMP_NF", "AP_NF", "EMPFLAG"]
# Flags that mean "Census didn't publish this number". (G, H and J only say how much noise was added.)
WITHHELD_FLAGS = {"D", "S"}
OUTPUT_COLUMNS = [
    "area_fips",
    "year",
    "naics_code",
    "industry_code",
    "employment",
    "employment_flag",
    "annual_payroll",
    "payroll_flag",
    "establishments",
    "is_withheld",
]

# CBP writes all industries as "------" and a two-digit sector as "23----". For sectors that QCEW groups
# together, CBP uses the first code of the group, so 31 stands for 31-33. 32, 33, 45 and 49 are ignored,
# which keeps a group from being counted twice if a file ever lists them too.
SECTOR_TO_QCEW = {
    "11": "11",
    "21": "21",
    "22": "22",
    "23": "23",
    "31": "31-33",
    "42": "42",
    "44": "44-45",
    "48": "48-49",
    "51": "51",
    "52": "52",
    "53": "53",
    "54": "54",
    "55": "55",
    "56": "56",
    "61": "61",
    "62": "62",
    "71": "71",
    "72": "72",
    "81": "81",
}
TOTAL_CODE = "10"


class CbpError(Exception):
    """Raised when the comparison can't be done at all (no QCEW data yet, for example)."""


def cbp_url(year, cfg):
    return cfg["urls"]["cbp_county_file"].format(year=year, yy=f"{year % 100:02d}")


def cbp_published(year, cfg):
    """True if Census has published the county file for this year. Raises CheckError if Census can't be reached."""
    return years.year_exists(year, cfg, "cbp_county_file")


def download_cbp_file(year, cfg):
    """Download one year's CBP county zip (or reuse it if it's already here). Returns a dict describing the file."""
    url = cbp_url(year, cfg)
    raw_dir = Path(cfg["paths"]["raw_dir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / f"cbp{year % 100:02d}co.zip"
    if dest.exists() and cfg["download"]["reuse_existing_files"]:
        log.info("CBP %s: already on disk, reusing %s", year, dest.name)
        status = "reused_existing"
    else:
        status = collect._download_with_retries(url, dest, cfg, year)
    return {
        "year": year,
        "url": url,
        "local_path": str(dest),
        "local_name": dest.name,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "download_status": status,
        "size_bytes": dest.stat().st_size,
        "sha256": collect.sha256_of_file(dest),
    }


# ---------------------------------------------------------------- reading the file
_TOTAL = r"[-/]{2,6}"
_SECTOR = r"(\d{2})[-/]{4}"


def read_cbp_year(zip_path, cfg):
    """Read one year's zip file. Returns (rows, stats).

    Keeps the county rows for the configured states: all industries, and each two-digit sector that maps to
    a QCEW sector. Raises ValueError if the file layout isn't what I expect, so a changed Census format fails
    loudly instead of quietly producing garbage.
    """
    states = set(cfg["state_fips"])
    kept, scanned, ignored = [], 0, set()
    with zipfile.ZipFile(zip_path) as z:
        members = [n for n in z.namelist() if n.lower().endswith((".txt", ".csv"))]
        if not members:
            raise ValueError(f"{Path(zip_path).name} has no .txt or .csv file inside.")
        with z.open(members[0]) as f:
            reader = pd.read_csv(f, dtype=str, keep_default_na=False, chunksize=200_000)
            for chunk in reader:
                chunk.columns = [str(c).strip().upper() for c in chunk.columns]
                missing = [c for c in REQUIRED_COLUMNS if c not in chunk.columns]
                if missing:
                    raise ValueError(f"The CBP file is missing expected columns: {missing}. Census may have changed it")
                scanned += len(chunk)
                naics = chunk["NAICS"].str.strip()
                is_total = naics.str.fullmatch(_TOTAL)
                prefix = naics.str.extract(f"^{_SECTOR}$")[0]
                is_sector = prefix.notna()
                inside = chunk["FIPSTATE"].str.strip().isin(states) & (chunk["FIPSCTY"].str.strip() != "999")
                known = prefix.isin(set(SECTOR_TO_QCEW))
                wanted = inside & (is_total | (is_sector & known))
                ignored |= set(prefix[inside & is_sector & ~known].dropna())
                kept.append(chunk[wanted])
    columns = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
    rows = pd.concat(kept, ignore_index=True) if kept else pd.DataFrame(columns=columns)
    stats = {"rows_scanned": scanned, "rows_kept": len(rows), "ignored_sector_codes": sorted(ignored)}
    return rows, stats


def _blank_to_none(series):
    """Empty text becomes a real missing value, so the database stores NULL and not an empty string."""
    return series.astype(object).where(series != "", None)


def _text(raw, name):
    """A text column from the raw rows, or an empty one if the file doesn't have it (older layouts)."""
    if name in raw.columns:
        return raw[name].str.strip().str.upper()
    return pd.Series("", index=raw.index)


def standardize_cbp(raw, year):
    """Turn the raw rows into the columns the database uses: plain names, real numbers, withheld values empty."""
    naics = raw["NAICS"].str.strip()
    prefix = naics.str.extract(f"^{_SECTOR}$")[0]
    industry = prefix.map(SECTOR_TO_QCEW).where(prefix.notna(), TOTAL_CODE)

    emp_flag, ap_flag, size_flag = _text(raw, "EMP_NF"), _text(raw, "AP_NF"), _text(raw, "EMPFLAG")
    # An EMPFLAG (older files) means the employment size class is shown in place of a number.
    employment_withheld = emp_flag.isin(WITHHELD_FLAGS) | (size_flag != "")
    payroll_withheld = ap_flag.isin(WITHHELD_FLAGS) | (size_flag != "")

    employment = pd.to_numeric(raw["EMP"], errors="coerce").where(~employment_withheld)
    payroll = (pd.to_numeric(raw["AP"], errors="coerce") * 1000).where(~payroll_withheld)  # Census reports $1,000s

    out = pd.DataFrame(
        {
            "area_fips": raw["FIPSTATE"].str.strip() + raw["FIPSCTY"].str.strip(),
            "year": year,
            "naics_code": naics,
            "industry_code": industry,
            "employment": employment,
            "employment_flag": _blank_to_none(emp_flag.where(emp_flag != "", size_flag)),
            "annual_payroll": payroll,
            "payroll_flag": _blank_to_none(ap_flag),
            "establishments": pd.to_numeric(raw["EST"], errors="coerce").astype("Int64"),
            "is_withheld": employment_withheld.astype(int),
        }
    )
    return out[OUTPUT_COLUMNS]


# ---------------------------------------------------------------- saving it
def load_cbp_year(conn, df, info, stats, cfg):
    """Replace this year's rows for the configured states with the new ones. Returns how many rows were saved.

    It all happens in one transaction, so a failure leaves the old rows in place.
    """
    key = ["area_fips", "year", "naics_code"]
    if df.duplicated(key).any():
        raise ValueError("The CBP file has more than one row for the same county and industry. Census changed it?")
    states = list(cfg["state_fips"])
    marks = ",".join("?" for _ in states)
    cleaned = df.astype(object).where(df.notna(), None)
    with conn:
        cur = conn.execute(
            """INSERT INTO cbp_files (year, url, local_name, retrieved_at, download_status, size_bytes, sha256,
                                       rows_scanned, rows_kept, loaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                info["year"],
                info["url"],
                info["local_name"],
                info["retrieved_at"],
                info["download_status"],
                info["size_bytes"],
                info["sha256"],
                stats["rows_scanned"],
                stats["rows_kept"],
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        file_id = cur.lastrowid
        conn.execute(
            f"DELETE FROM cbp_county WHERE year = ? AND substr(area_fips, 1, 2) IN ({marks})", [info["year"], *states]
        )
        conn.executemany(
            """INSERT INTO cbp_county (area_fips, year, naics_code, industry_code, employment, employment_flag,
                                       annual_payroll, payroll_flag, establishments, is_withheld, cbp_file_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(*row, file_id) for row in cleaned[OUTPUT_COLUMNS].itertuples(index=False, name=None)],
        )
    return len(cleaned)


def years_to_compare(conn, cfg):
    """The years the database already has QCEW rows for, inside the configured states."""
    states = list(cfg["state_fips"])
    marks = ",".join("?" for _ in states)
    rows = conn.execute(
        f"SELECT DISTINCT year FROM qcew_annual WHERE substr(area_fips, 1, 2) IN ({marks}) ORDER BY year", states
    ).fetchall()
    return [int(r[0]) for r in rows]


def run_cbp(cfg, requested_years=None):
    """Download, load and compare. Returns a dict of what happened. Raises CbpError if there is nothing to compare."""
    conn = load.connect(cfg["paths"]["db_path"])
    try:
        load.init_schema(conn)
        in_database = years_to_compare(conn, cfg)
        if not in_database:
            raise CbpError("The database has no QCEW rows for these states yet. Run qcew-run first.")
        wanted = [y for y in (requested_years or in_database) if y in in_database]
        if not wanted:
            raise CbpError(f"None of the years {sorted(requested_years or [])} are in the database ({in_database}).")

        loaded, not_published, ignored_codes = {}, [], set()
        for year in wanted:
            if not cbp_published(year, cfg):
                log.info("CBP %s: not published yet, skipping", year)
                not_published.append(year)
                continue
            info = download_cbp_file(year, cfg)
            raw, stats = read_cbp_year(info["local_path"], cfg)
            ignored_codes |= set(stats["ignored_sector_codes"])
            loaded[year] = load_cbp_year(conn, standardize_cbp(raw, year), info, stats, cfg)
            log.info("CBP %s: %s rows saved", year, f"{loaded[year]:,}")

        if not loaded:
            raise CbpError(
                f"Census hasn't published a County Business Patterns file for any of {wanted} yet. Try again later."
            )
        results = cross_source.compare(conn, cfg, sorted(loaded))
        cross_source.store_results(conn, results)
        report = cross_source.write_report(cfg, results, sorted(loaded), not_published, sorted(ignored_codes))
    finally:
        conn.close()
    return {
        "loaded_years": sorted(loaded),
        "rows_saved": loaded,
        "not_published": not_published,
        "ignored_sector_codes": sorted(ignored_codes),
        "results": [r.as_dict() for r in results],
        "report": str(report),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download Census County Business Patterns and compare it with QCEW.")
    parser.add_argument("--config", help="path to a config.toml (default: the one inside the package)")
    add_region_argument(parser)
    parser.add_argument("--years", nargs="+", type=int, help="only these years (default: every year in the database)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config, args.region)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    try:
        result = run_cbp(cfg, args.years)
    except CbpError as err:
        print(err, file=sys.stderr)
        return 2
    except (collect.DownloadError, years.CheckError, ValueError) as err:
        print(f"Could not finish: {err}", file=sys.stderr)
        return 1

    span = f"{result['loaded_years'][0]}-{result['loaded_years'][-1]}"
    print(f"Compared QCEW with County Business Patterns for {span}. Report: {result['report']}")
    if result["not_published"]:
        print(f"Census hasn't published {result['not_published']} yet, so those years weren't compared.")
    warned = [r for r in result["results"] if r["level"] == "warn" and not r["passed"]]
    for r in warned:
        print(f"  WARN {r['check_name']}: {r['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
