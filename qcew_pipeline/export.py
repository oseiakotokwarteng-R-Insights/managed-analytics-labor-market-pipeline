"""Step 5: hand the data to people as plain CSV files.

Anyone who doesn't want to touch SQL can open these in Excel. Each file has a
suppressed flag, because a blank cell means "BLS didn't publish this", not zero.
"""

import logging
from pathlib import Path

import pandas as pd

from . import load

log = logging.getLogger("qcew")

QUERIES = {
    "county_private_totals": """
        SELECT year, state_fips, area_fips, area_title, establishments, employment,
               avg_weekly_wage, is_suppressed
        FROM v_county_totals
        ORDER BY state_fips, area_fips, year""",
    "county_private_sectors": """
        SELECT year, state_fips, area_fips, area_title, industry_code, industry_title,
               employment, avg_weekly_wage, is_suppressed
        FROM v_county_sectors
        ORDER BY state_fips, area_fips, industry_code, year""",
}


def export_all(cfg, out_dir=None):
    out_dir = Path(out_dir or Path(cfg["paths"]["output_dir"]) / "exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = load.connect(cfg["paths"]["db_path"])
    written = []
    try:
        for name, sql in QUERIES.items():
            df = pd.read_sql_query(sql, conn)
            path = out_dir / f"{name}.csv"
            df.to_csv(path, index=False)
            log.info("exported %s (%s rows)", path.name, f"{len(df):,}")
            written.append(path)
    finally:
        conn.close()
    return written
