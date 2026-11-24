"""Shared test setup: a tiny made-up dataset that looks like the real one.

I use fake data here on purpose. The tests need to run in seconds (and in
GitHub Actions) without downloading 75 MB files from BLS, and I need to be able
to break the data in exactly the ways I want to test.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

# Make "import qcew_pipeline" work when pytest runs from the project folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qcew_pipeline.config import load_config  # noqa: E402
from qcew_pipeline.transform import OUTPUT_COLUMNS  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config()
    cfg["years"] = [2023, 2024]            # a fixed list, so the tests never ask BLS which year is newest
    cfg["paths"]["db_path"] = tmp_path / "test.sqlite"
    cfg["paths"]["raw_dir"] = tmp_path / "raw"
    cfg["paths"]["output_dir"] = tmp_path / "out"
    # The real files have thousands of rows; my fake data has a handful.
    cfg["validation"]["min_rows_per_year"] = 1
    cfg["validation"]["expected_county_counts"] = {"11": 1, "24": 2, "51": 1}
    return cfg


@pytest.fixture
def refs():
    return {
        "area": pd.DataFrame({
            "area_fips": ["11000", "11001", "24000", "24001", "24003", "51000", "51013"],
            "area_title": ["DC", "District of Columbia", "Maryland", "Allegany", "Anne Arundel",
                           "Virginia", "Arlington"]}),
        "industry": pd.DataFrame({"industry_code": ["10", "23", "62"],
                                  "industry_title": ["Total", "Construction", "Health care"]}),
        "ownership": pd.DataFrame({"own_code": ["0", "5"], "own_title": ["Total", "Private"]}),
        "agglevel": pd.DataFrame({"agglvl_code": ["51", "71", "74"],
                                  "agglvl_title": ["State total", "County total", "County sector"]}),
    }


def make_row(area, year, agglvl, industry, employment, wage=1000, suppressed=0):
    return {
        "area_fips": area, "year": year, "own_code": "5", "industry_code": industry,
        "agglvl_code": agglvl, "disclosure_code": "N" if suppressed else None,
        "is_suppressed": suppressed,
        "establishments": None if suppressed else 10.0,
        "employment": None if suppressed else float(employment),
        "total_wages": None if suppressed else float(employment) * wage * 52,
        "avg_weekly_wage": None if suppressed else float(wage),
        "avg_annual_pay": None if suppressed else float(wage) * 52,
    }


@pytest.fixture
def clean_df():
    """Two years of consistent fake data. State totals equal the sum of their counties."""
    rows = []
    for year in (2023, 2024):
        # DC: one county, which is also the whole state
        rows += [make_row("11000", year, "51", "10", 100), make_row("11001", year, "71", "10", 100),
                 make_row("11001", year, "74", "23", 40), make_row("11001", year, "74", "62", 60)]
        # Maryland: two counties
        rows += [make_row("24000", year, "51", "10", 300), make_row("24001", year, "71", "10", 100),
                 make_row("24003", year, "71", "10", 200),
                 make_row("24001", year, "74", "23", 100), make_row("24003", year, "74", "62", 150),
                 make_row("24003", year, "74", "23", 50)]
        # Virginia: one county
        rows += [make_row("51000", year, "51", "10", 50), make_row("51013", year, "71", "10", 50),
                 make_row("51013", year, "74", "23", 50)]
    return pd.DataFrame(rows)[OUTPUT_COLUMNS]
