"""How long does the database load take as the data gets bigger?

Builds made-up data of any size (no BLS download) and times three loads:

    python evaluation/benchmark_load.py --rows 600000

  1. the first load, into an empty database
  2. the same data again (nothing should change)
  3. a typical BLS revision: 1% of the rows get new values and 0.5% disappear

It prints one line per load. Run it from the project folder.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qcew_pipeline import load  # noqa: E402
from qcew_pipeline.config import load_config  # noqa: E402
from qcew_pipeline.transform import OUTPUT_COLUMNS  # noqa: E402

YEARS = [2020, 2021, 2022, 2023, 2024]
INDUSTRIES = [str(100 + i) for i in range(50)]


def make_data(rows, seed=7):
    """Made-up rows shaped like the real table: area x year x industry."""
    per_state = max(1, rows // (len(YEARS) * len(INDUSTRIES) * 3))
    if per_state > 999:
        raise SystemExit("Use --rows 750000 or fewer (the area codes would run out).")
    areas = [f"{s}{i:03d}" for s in ("11", "24", "51") for i in range(1, per_state + 1)]
    index = pd.MultiIndex.from_product([areas, YEARS, INDUSTRIES], names=["area_fips", "year", "industry_code"])
    df = index.to_frame(index=False)
    rng = np.random.default_rng(seed)
    n = len(df)
    df["own_code"] = "5"
    df["agglvl_code"] = "74"
    df["disclosure_code"] = None
    df["is_suppressed"] = 0
    df["establishments"] = rng.integers(1, 500, n).astype(float)
    df["employment"] = rng.integers(1, 20000, n).astype(float)
    df["avg_weekly_wage"] = rng.integers(400, 4000, n).astype(float)
    df["total_wages"] = df["employment"] * df["avg_weekly_wage"] * 52
    df["avg_annual_pay"] = df["avg_weekly_wage"] * 52
    return df[OUTPUT_COLUMNS], areas


def timed_load(conn, df, cfg, label):
    run_id = load.start_run(conn, YEARS)
    ids = {}
    for year in YEARS:
        info = dict(year=year, url="http://example", local_name="x.zip", retrieved_at="now",
                    download_status="bench", size_bytes=1, sha256="abc")
        ids[year] = load.record_source_file(conn, run_id, info, 1, 1)
    t = time.perf_counter()
    counts = load.load_facts(conn, df, run_id, ids, cfg)
    seconds = time.perf_counter() - t
    print(f"{label:<28} {seconds:8.1f} s   {counts}", flush=True)
    return seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=300_000)
    args = parser.parse_args()

    cfg = load_config()
    df, areas = make_data(args.rows)
    print(f"{len(df):,} rows, {len(areas):,} areas, {len(YEARS)} years, {len(INDUSTRIES)} industries", flush=True)

    refs = {
        "area": pd.DataFrame({"area_fips": areas, "area_title": areas}),
        "industry": pd.DataFrame({"industry_code": INDUSTRIES, "industry_title": INDUSTRIES}),
        "ownership": pd.DataFrame({"own_code": ["5"], "own_title": ["Private"]}),
        "agglevel": pd.DataFrame({"agglvl_code": ["74"], "agglvl_title": ["County sector"]}),
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg["paths"]["db_path"] = Path(tmp) / "bench.sqlite"
        conn = load.connect(cfg["paths"]["db_path"])
        load.init_schema(conn)
        load.load_reference_tables(conn, refs, cfg)

        timed_load(conn, df, cfg, "1. first load")
        timed_load(conn, df, cfg, "2. same data again")

        revised = df.copy()
        rng = np.random.default_rng(1)
        pick = rng.choice(len(revised), size=len(revised) // 100, replace=False)
        revised.loc[revised.index[pick], "employment"] += 5
        revised = revised.drop(index=revised.index[rng.choice(len(revised), size=len(revised) // 200, replace=False)])
        timed_load(conn, revised, cfg, "3. 1% revised, 0.5% removed")
        conn.close()


if __name__ == "__main__":
    main()
