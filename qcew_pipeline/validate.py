"""Step 3: check the data before it's allowed into the database.

Every check returns a CheckResult. There are two levels that matter:

  error - something is wrong enough that the data shouldn't be loaded
  warn  - worth a human looking at it, but the load can go ahead

"info" results are just facts worth recording, like how many values were suppressed.
Nothing here modifies the data; it only reports.
"""

from dataclasses import asdict, dataclass

import pandas as pd

from .transform import MEASURES, area_type

KEY = ["area_fips", "year", "own_code", "industry_code"]


@dataclass
class CheckResult:
    name: str
    level: str  # "error", "warn" or "info"
    passed: bool
    failed_count: int
    detail: str

    def as_dict(self):
        return asdict(self)


def _result(name, level, failed_count, detail_if_fail, detail_if_pass="ok"):
    passed = failed_count == 0
    return CheckResult(name, level, passed, int(failed_count), detail_if_pass if passed else detail_if_fail)


def run_checks(df, refs, cfg, years_expected, rows_by_year=None, scanned_by_year=None):
    """Run every check and return the list of results.

    df             - the standardized table I'm about to load
    refs           - the BLS code/title lookup tables
    years_expected - the years the config asked for
    rows_by_year   - how many rows each year's file produced (for the completeness check)
    scanned_by_year - how many rows BLS's national file had for each year (for the drift check)
    """
    v = cfg["validation"]
    results = []

    # ---- completeness: did every year show up with a believable number of rows? ----
    rows_by_year = rows_by_year or df.groupby("year").size().to_dict()
    short = [y for y in years_expected if rows_by_year.get(y, 0) < v["min_rows_per_year"]]
    results.append(
        _result(
            "all_years_present_with_enough_rows",
            "error",
            len(short),
            f"Years missing or too small (< {v['min_rows_per_year']} rows): {short}",
            f"all {len(years_expected)} years present",
        )
    )

    # ---- required fields ----
    null_keys = int(df[KEY + ["agglvl_code"]].isna().any(axis=1).sum()) if len(df) else 0
    results.append(
        _result("required_fields_not_null", "error", null_keys, f"{null_keys} rows have a missing key field")
    )

    # ---- duplicates at the level I actually care about ----
    dups = int(df.duplicated(subset=KEY, keep=False).sum())
    results.append(
        _result("no_duplicate_keys", "error", dups, f"{dups} rows share an (area, year, ownership, industry) key")
    )

    # ---- codes exist in BLS's own lookup tables ----
    valid_areas = set(refs["area"]["area_fips"])
    bad_area = int((~df["area_fips"].isin(valid_areas)).sum())
    results.append(
        _result("valid_area_codes", "error", bad_area, f"{bad_area} rows have an area code not in the BLS area list")
    )

    valid_ind = set(refs["industry"]["industry_code"])
    bad_ind = int((~df["industry_code"].isin(valid_ind)).sum())
    results.append(
        _result(
            "valid_industry_codes",
            "error",
            bad_ind,
            f"{bad_ind} rows have an industry code not in the BLS industry list",
        )
    )

    valid_own = set(refs["ownership"]["own_code"])
    bad_own = int((~df["own_code"].isin(valid_own)).sum())
    results.append(
        _result(
            "valid_ownership_codes",
            "error",
            bad_own,
            f"{bad_own} rows have an ownership code not in the BLS ownership list",
        )
    )

    bad_state = int((~df["area_fips"].str[:2].isin(cfg["state_fips"])).sum())
    results.append(
        _result("areas_inside_requested_states", "error", bad_state, f"{bad_state} rows are outside DC/MD/VA")
    )

    bad_year = int((~df["year"].isin(years_expected)).sum())
    results.append(
        _result("years_inside_requested_range", "error", bad_year, f"{bad_year} rows have a year I didn't ask for")
    )

    # ---- suppression: unknown must never be stored as zero ----
    suppressed = df["is_suppressed"] == 1
    zero_suppressed = int((suppressed & df[MEASURES].notna().any(axis=1)).sum())
    results.append(
        _result(
            "suppressed_values_are_null_not_zero",
            "error",
            zero_suppressed,
            f"{zero_suppressed} suppressed rows still carry numbers",
        )
    )

    results.append(
        CheckResult(
            "suppression_rate",
            "info",
            True,
            int(suppressed.sum()),
            f"{int(suppressed.sum()):,} of {len(df):,} rows suppressed "
            f"({(suppressed.mean() * 100 if len(df) else 0):.1f}%)",
        )
    )

    # ---- numbers make sense ----
    negatives = int((df[MEASURES] < 0).any(axis=1).sum())
    results.append(
        _result("no_negative_measures", "error", negatives, f"{negatives} rows have a negative count or wage")
    )

    both = df["avg_weekly_wage"].notna() & df["avg_annual_pay"].notna()
    gap = (df.loc[both, "avg_annual_pay"] - 52 * df.loc[both, "avg_weekly_wage"]).abs()
    wage_mismatch = int((gap > v["wage_check_tolerance_dollars"]).sum())
    results.append(
        _result(
            "annual_pay_matches_weekly_wage",
            "warn",
            wage_mismatch,
            f"{wage_mismatch} rows where annual pay is more than "
            f"${v['wage_check_tolerance_dollars']} away from 52 x weekly wage",
        )
    )

    # ---- geography coverage ----
    results.extend(_coverage_checks(df, cfg))

    # ---- agreement with BLS's own published totals ----
    results.append(_state_reconciliation(df, v["state_reconciliation_tolerance"]))
    results.append(_sector_vs_total(df, v["sector_sum_tolerance"]))

    # ---- drift: does each year look like the one before it? (warnings only) ----
    results.append(_suppression_drift(df, v.get("suppression_jump_points", 5.0)))
    results.append(_source_volume_drift(scanned_by_year, v.get("source_rows_change_fraction", 0.10)))

    return results


def _coverage_checks(df, cfg):
    """Did I get the right number of counties for each state, in every year?"""
    out = []
    county_totals = df[(df["agglvl_code"] == "71")].copy()
    county_totals = county_totals[county_totals["area_fips"].map(area_type) == "county"]
    expected = cfg["validation"]["expected_county_counts"]

    wrong = []
    for year, g in county_totals.groupby("year"):
        counts = g["area_fips"].str[:2].value_counts()
        for state, want in expected.items():
            if state in cfg["state_fips"] and counts.get(state, 0) != want:
                wrong.append((int(year), state, int(counts.get(state, 0)), want))
    out.append(
        _result(
            "expected_county_counts_each_year",
            "warn",
            len(wrong),
            f"State/year combos with a different county count than expected (year, state, found, expected): {wrong}",
            "county counts match expectations every year",
        )
    )

    # Is the same set of counties present in every year?
    sets = county_totals.groupby("year")["area_fips"].apply(set)
    if len(sets):
        union = set.union(*sets.values)
        inconsistent = sorted(a for a in union if any(a not in s for s in sets.values))
    else:
        inconsistent = []
    out.append(
        _result(
            "same_counties_in_every_year",
            "warn",
            len(inconsistent),
            f"Counties missing in at least one year: {inconsistent}",
            "same counties appear in every year",
        )
    )
    return out


def _state_reconciliation(df, tolerance):
    """Add up the county totals and compare them to BLS's own state total."""
    totals = df[df["industry_code"] == "10"]
    # groupby().first() so that duplicate rows (which another check reports) can't crash this one.
    state = totals[totals["agglvl_code"] == "51"].groupby(["area_fips", "year"])["employment"].first()
    county = totals[totals["agglvl_code"] == "71"].copy()
    county["state_area"] = county["area_fips"].str[:2] + "000"
    summed = county.groupby(["state_area", "year"])["employment"].sum(min_count=1)
    summed.index.names = ["area_fips", "year"]

    joined = pd.concat([summed.rename("county_sum"), state.rename("state_total")], axis=1).dropna()
    if joined.empty:
        return CheckResult(
            "county_totals_agree_with_state_total", "warn", False, 1, "no state/county pairs available to compare"
        )
    joined["rel_diff"] = (joined["county_sum"] - joined["state_total"]).abs() / joined["state_total"]
    bad = joined[joined["rel_diff"] > tolerance]
    worst = joined["rel_diff"].max()
    return _result(
        "county_totals_agree_with_state_total",
        "warn",
        len(bad),
        f"{len(bad)} state-years differ by more than {tolerance:.0%}; worst gap {worst:.2%}",
        f"all {len(joined)} state-years within {tolerance:.0%} (worst gap {worst:.2%})",
    )


def _sector_vs_total(df, tolerance):
    """Sector employment shouldn't add up to MORE than the county total."""
    total = df[df["agglvl_code"] == "71"].groupby(["area_fips", "year"])["employment"].first()
    sectors = df[df["agglvl_code"] == "74"].groupby(["area_fips", "year"])["employment"].sum(min_count=1)
    joined = pd.concat([sectors.rename("sector_sum"), total.rename("county_total")], axis=1).dropna()
    if joined.empty:
        return CheckResult(
            "sector_sum_not_above_county_total", "warn", False, 1, "no county/sector pairs available to compare"
        )
    too_big = joined[joined["sector_sum"] > joined["county_total"] * (1 + tolerance)]
    return _result(
        "sector_sum_not_above_county_total",
        "warn",
        len(too_big),
        f"{len(too_big)} county-years where sector employment exceeds the county total",
        f"checked {len(joined):,} county-years; none above the county total",
    )


def _suppression_drift(df, max_jump_points):
    """Warn if the share of hidden (suppressed) values jumps up from one year to the next."""
    name = "suppression_rate_stable"
    rate = (df.groupby("year")["is_suppressed"].mean() * 100).to_dict()
    years = sorted(rate)
    if len(years) < 2:
        return CheckResult(name, "warn", True, 0, "needs at least two years to compare")
    jumps = []
    for prev, cur in zip(years, years[1:]):
        rise = rate[cur] - rate[prev]
        if rise > max_jump_points:
            jumps.append(f"{int(cur)}: {rate[cur]:.1f}% hidden, up {rise:.1f} points from {int(prev)}")
    return _result(
        name,
        "warn",
        len(jumps),
        "; ".join(jumps) + f" (limit {max_jump_points:g} points)",
        f"no year-to-year jump above {max_jump_points:g} points",
    )


def _source_volume_drift(scanned_by_year, max_change):
    """Warn if a year's national file from BLS has many more or fewer rows than the year before."""
    name = "source_rows_stable"
    if not scanned_by_year or len(scanned_by_year) < 2:
        return CheckResult(name, "warn", True, 0, "needs row counts for at least two years to compare")
    years = sorted(scanned_by_year)
    changes = []
    for prev, cur in zip(years, years[1:]):
        before, now = scanned_by_year[prev], scanned_by_year[cur]
        if before and abs(now - before) / before > max_change:
            share = abs(now - before) / before
            word = "fewer" if now < before else "more"
            changes.append(f"{cur}: {now:,} rows in the BLS file, {share:.1%} {word} than {prev} ({before:,})")
    return _result(
        name,
        "warn",
        len(changes),
        "; ".join(changes) + f" (limit {max_change:.0%})",
        f"no year-to-year change above {max_change:.0%}",
    )


def summarize(results):
    """Count errors and warnings so the pipeline can decide whether to load."""
    errors = sum(1 for r in results if r.level == "error" and not r.passed)
    warnings = sum(1 for r in results if r.level == "warn" and not r.passed)
    return {"errors": errors, "warnings": warnings, "checks_run": len(results)}
