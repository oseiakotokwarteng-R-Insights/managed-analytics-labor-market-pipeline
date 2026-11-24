"""Step 2: turn a giant national file into a small, tidy DMV table.

The BLS annual file has every county in the country and a lot of columns I
don't need. I read it in chunks (so it never has to fit in memory), keep only
the rows that match my config, rename the columns to plain names, and fix the
data types. The one rule I care most about is in here too: suppressed values
become NULL, never zero.
"""

import logging
import zipfile
from pathlib import Path

import pandas as pd

log = logging.getLogger("qcew")

# The columns I actually need from the BLS file.
REQUIRED_RAW_COLUMNS = [
    "area_fips",
    "own_code",
    "industry_code",
    "agglvl_code",
    "size_code",
    "year",
    "qtr",
    "disclosure_code",
    "annual_avg_estabs",
    "annual_avg_emplvl",
    "total_annual_wages",
    "annual_avg_wkly_wage",
    "avg_annual_pay",
]

# BLS name -> the plainer name I use in my database.
RENAME = {
    "annual_avg_estabs": "establishments",
    "annual_avg_emplvl": "employment",
    "total_annual_wages": "total_wages",
    "annual_avg_wkly_wage": "avg_weekly_wage",
    "avg_annual_pay": "avg_annual_pay",
}

MEASURES = ["establishments", "employment", "total_wages", "avg_weekly_wage", "avg_annual_pay"]

# This is the order the final table's columns come out in.
OUTPUT_COLUMNS = [
    "area_fips",
    "year",
    "own_code",
    "industry_code",
    "agglvl_code",
    "disclosure_code",
    "is_suppressed",
] + MEASURES


def check_raw_columns(header_columns):
    """Return the list of required columns that are missing from the BLS file."""
    return [c for c in REQUIRED_RAW_COLUMNS if c not in header_columns]


def filter_chunk(chunk, cfg):
    """Keep only the rows I want from one chunk of the raw file."""
    # 5-digit numeric codes only. This drops metro areas, combined areas and the national row.
    is_five_digits = chunk["area_fips"].str.fullmatch(r"\d{5}")
    in_states = chunk["area_fips"].str[:2].isin(cfg["state_fips"])
    keep = (
        is_five_digits
        & in_states
        & chunk["agglvl_code"].isin(cfg["agglvl_codes"])
        & chunk["own_code"].isin(cfg["ownership_codes"])
        & (chunk["size_code"] == cfg["size_code"])
    )
    return chunk.loc[keep]


def read_year(zip_path, cfg):
    """Read one year's zip file and return (filtered DataFrame, stats dict).

    Raises ValueError if the file layout isn't what I expect, so a changed
    BLS format fails loudly instead of quietly producing garbage.
    """
    with zipfile.ZipFile(zip_path) as z:
        csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"Expected one CSV inside {zip_path}, found {csv_names}")
        member = csv_names[0]

        with z.open(member) as f:
            header = pd.read_csv(f, nrows=0).columns.tolist()
        missing = check_raw_columns(header)
        if missing:
            raise ValueError(f"{zip_path} is missing expected columns: {missing}")

        kept_parts, rows_scanned = [], 0
        with z.open(member) as f:
            reader = pd.read_csv(f, dtype=str, usecols=REQUIRED_RAW_COLUMNS, chunksize=500_000)
            for chunk in reader:
                rows_scanned += len(chunk)
                kept_parts.append(filter_chunk(chunk, cfg))

    kept = pd.concat(kept_parts, ignore_index=True) if kept_parts else pd.DataFrame(columns=REQUIRED_RAW_COLUMNS)
    log.info("%s: scanned %s rows, kept %s", Path(zip_path).name, f"{rows_scanned:,}", f"{len(kept):,}")
    return kept, {"rows_scanned": rows_scanned, "rows_kept": len(kept)}


def standardize(raw):
    """Rename columns, fix types, and handle suppressed values.

    BLS marks hidden values with disclosure_code = 'N' and puts a 0 in the
    number columns. A zero there is misleading (the real value isn't zero, it's
    just secret), so I blank those out to NULL and keep a flag.
    """
    df = raw.rename(columns=RENAME).copy()
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")

    for col in MEASURES:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["disclosure_code"] = df["disclosure_code"].where(df["disclosure_code"].notna(), None)
    df["is_suppressed"] = (df["disclosure_code"] == "N").astype(int)

    # The key step: suppressed means unknown, not zero.
    df[MEASURES] = df[MEASURES].mask(df["is_suppressed"] == 1)

    return df[OUTPUT_COLUMNS].reset_index(drop=True)


def area_type(area_fips):
    """Label an area code as a state row, an 'undefined county' bucket, or a real county."""
    if area_fips.endswith("000"):
        return "state"
    if area_fips.endswith("999"):
        return "undefined"
    return "county"
