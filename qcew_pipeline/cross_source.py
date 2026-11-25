"""Compare QCEW with County Business Patterns, and write down what I find.

The two sources count jobs in different ways, so I don't expect them to match. I expect them to be close.
These are the differences that matter most:

  - CBP counts employees in the week of March 12. QCEW averages the whole year.
  - CBP leaves out some kinds of work, like most government jobs and farm jobs. I only use QCEW's private
    sector rows, and CBP doesn't cover farms, so agriculture is the sector most likely to disagree.
  - Since 2020 Census adds a little random noise to the numbers it publishes.
  - Each source hides different numbers to protect businesses, so a county can have a number in one and not
    the other. I only compare rows where both sources show a number.

So the checks below only warn. A warning means "look at this", not "something is broken". The limits come
from config.toml, and I picked them as a starting point, not as anything official.
"""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .validate import _result

MAX_LISTED = 10


def _marks(values):
    return ",".join("?" for _ in values)


def _compared_rows(conn, cfg, years):
    """Every county and industry both sources publish, for the years and states asked for."""
    states = list(cfg["state_fips"])
    sql = f"SELECT * FROM v_qcew_vs_cbp WHERE year IN ({_marks(years)}) AND state_fips IN ({_marks(states)})"
    return pd.read_sql_query(sql, conn, params=[*years, *states])


def _qcew_county_rows(conn, cfg, years):
    """How many private-sector county rows QCEW has for the same years and states, matched or not."""
    states = list(cfg["state_fips"])
    sql = f"""SELECT COUNT(*) FROM qcew_annual q JOIN dim_area a ON a.area_fips = q.area_fips
              WHERE a.area_type = 'county' AND q.own_code = '5' AND q.agglvl_code IN ('71', '74')
                AND q.year IN ({_marks(years)}) AND a.state_fips IN ({_marks(states)})"""
    return int(conn.execute(sql, [*years, *states]).fetchone()[0])


def _ratio_by_state_year(df, cbp_col, qcew_col):
    """CBP divided by QCEW, added up over the counties for each state and year."""
    grouped = df.groupby(["state_fips", "year"])[[cbp_col, qcew_col]].sum()
    return (grouped[cbp_col] / grouped[qcew_col]).dropna()


def _listing(items):
    shown = "; ".join(items[:MAX_LISTED])
    more = f" (and {len(items) - MAX_LISTED} more)" if len(items) > MAX_LISTED else ""
    return shown + more


def compare(conn, cfg, years):
    """Run the comparison. Returns a list of CheckResult, the same kind the validation step uses."""
    limits = cfg["cbp"]
    df = _compared_rows(conn, cfg, years)
    total_qcew = _qcew_county_rows(conn, cfg, years)
    results = []

    share = 100 * len(df) / total_qcew if total_qcew else 0
    results.append(
        _result(
            "cbp_rows_matched",
            "info",
            0,
            "",
            f"{len(df):,} of {total_qcew:,} QCEW county rows for {years[0]}-{years[-1]} have a County Business "
            f"Patterns row for the same county and industry ({share:.0f}%).",
        )
    )

    both = df[
        (df["qcew_suppressed"] == 0)
        & (df["cbp_withheld"] == 0)
        & df["qcew_employment"].notna()
        & df["cbp_employment"].notna()
    ]
    totals = both[both["industry_code"] == "10"]

    # ---- the whole state, added up from its counties
    if totals.empty:
        results.append(
            _result(
                "cbp_state_employment_ratio",
                "warn",
                1,
                "No all-industry county row has an employment number in both sources.",
            )
        )
    else:
        ratios = _ratio_by_state_year(totals, "cbp_employment", "qcew_employment")
        tolerance = limits["state_employment_tolerance"]
        off = [f"{int(y)} state {s}: {r:.3f}" for (s, y), r in ratios.items() if abs(r - 1) > tolerance]
        summary = ", ".join(f"{int(y)} state {s}: {r:.3f}" for (s, y), r in ratios.items())
        results.append(
            _result(
                "cbp_state_employment_ratio",
                "warn",
                len(off),
                f"CBP employment is more than {tolerance:.0%} away from QCEW (CBP divided by QCEW): {_listing(off)}",
                f"CBP employment is within {tolerance:.0%} of QCEW for every state and year "
                f"(CBP divided by QCEW): {summary}",
            )
        )

    establishments = df[df["qcew_establishments"].notna() & df["cbp_establishments"].notna()]
    establishments = establishments[establishments["industry_code"] == "10"]
    if establishments.empty:
        results.append(
            _result(
                "cbp_state_establishment_ratio",
                "warn",
                1,
                "No all-industry county row has a business count in both sources.",
            )
        )
    else:
        ratios = _ratio_by_state_year(establishments, "cbp_establishments", "qcew_establishments")
        tolerance = limits["state_establishment_tolerance"]
        off = [f"{int(y)} state {s}: {r:.3f}" for (s, y), r in ratios.items() if abs(r - 1) > tolerance]
        summary = ", ".join(f"{int(y)} state {s}: {r:.3f}" for (s, y), r in ratios.items())
        results.append(
            _result(
                "cbp_state_establishment_ratio",
                "warn",
                len(off),
                f"The number of businesses is more than {tolerance:.0%} apart (CBP divided by QCEW): {_listing(off)}",
                f"The number of businesses is within {tolerance:.0%} for every state and year "
                f"(CBP divided by QCEW): {summary}",
            )
        )

    # ---- one county at a time (only counties big enough that the numbers should be steady)
    big = totals[totals["qcew_employment"] >= limits["county_min_employment"]].copy()
    big["ratio"] = big["cbp_employment"] / big["qcew_employment"]
    low, high = limits["county_ratio_low"], limits["county_ratio_high"]
    odd = big[(big["ratio"] < low) | (big["ratio"] > high)].copy()
    odd["distance"] = (odd["ratio"] - 1).abs()
    odd = odd.sort_values("distance", ascending=False)
    names = [f"{int(r.year)} {r.area_title} ({r.area_fips}): {r.ratio:.2f}" for r in odd.itertuples()]
    results.append(
        _result(
            "cbp_county_outliers",
            "warn",
            len(odd),
            f"Counties with at least {limits['county_min_employment']:,} jobs where CBP divided by QCEW is outside "
            f"{low}-{high}: {_listing(names)}",
            f"All {len(big):,} county-years with at least {limits['county_min_employment']:,} jobs have a CBP/QCEW "
            f"ratio between {low} and {high}.",
        )
    )

    # ---- which sectors agree least (only for information)
    sectors = both[both["industry_code"] != "10"].copy()
    if not sectors.empty:
        sectors["ratio"] = sectors["cbp_employment"] / sectors["qcew_employment"]
        sectors = sectors[sectors["qcew_employment"] > 0]
        medians = sectors.groupby("industry_code")["ratio"].agg(["median", "count"]).sort_index()
        text = ", ".join(f"{code}: {row['median']:.2f} ({int(row['count']):,})" for code, row in medians.iterrows())
        results.append(
            _result(
                "cbp_sector_ratios",
                "info",
                0,
                "",
                f"Median CBP divided by QCEW employment by sector, with the number of county-years in brackets: {text}",
            )
        )

    # ---- how much each source hides, for the rows both publish
    if len(df):
        cbp_hidden = 100 * float((df["cbp_withheld"] == 1).mean())
        qcew_hidden = 100 * float((df["qcew_suppressed"] == 1).mean())
        results.append(
            _result(
                "cbp_withheld_share",
                "info",
                0,
                "",
                f"Among the matched rows, Census hides {cbp_hidden:.0f}% of employment numbers and BLS hides "
                f"{qcew_hidden:.0f}%.",
            )
        )
    return results


def store_results(conn, results):
    """Keep every comparison in the database, so changes over time are easy to see."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    with conn:
        conn.executemany(
            "INSERT INTO cross_source_results (compared_at, check_name, level, passed, failed_count, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(stamp, r.name, r.level, int(r.passed), r.failed_count, r.detail) for r in results],
        )


def write_report(cfg, results, compared_years, not_published, ignored_codes):
    """Write the comparison as a short markdown report in the outputs folder. Returns its path."""
    out = Path(cfg["paths"]["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    path = out / "cbp_comparison.md"
    lines = [
        "# QCEW compared with County Business Patterns",
        "",
        f"Region: {cfg.get('region_name', 'unknown')}. Years compared: {compared_years[0]}-{compared_years[-1]}.",
        "",
        "The two sources count jobs in different ways, so I expect them to be close, not equal. "
        "A warning below means it's worth a look. It doesn't mean something is broken.",
        "",
        "| Check | Result | What it found |",
        "|---|---|---|",
    ]
    for r in results:
        status = "ok" if r.passed else "WARN"
        lines.append(f"| `{r.name}` | {status} | {r.detail.replace('|', '/')} |")
    if not_published:
        lines += [
            "",
            f"Census hasn't published County Business Patterns for {sorted(not_published)} yet, "
            "so those years were left out.",
        ]
    if ignored_codes:
        lines += ["", f"Two-digit industry codes in the Census file that I don't use: {ignored_codes}."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path
