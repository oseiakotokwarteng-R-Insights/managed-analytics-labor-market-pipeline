"""Tests for the second source: reading the Census file, saving it, comparing it with QCEW, and the command.

Nothing here downloads anything. The Census files are small made-up ones that follow the layout Census
documents (FIPSTATE, FIPSCTY, NAICS, EMP, EMP_NF, AP, AP_NF, EST and so on).
"""

import zipfile

import pandas as pd
import pytest

from qcew_pipeline import cbp, collect, cross_source, load
from qcew_pipeline.years import CheckError
from tests.test_load import fake_sources, fresh_db

# ---------------------------------------------------------------- made-up Census files


def census_row(state="24", county="003", naics="------", emp="500", emp_nf="G", ap="30000", ap_nf="G", est="40"):
    return {
        "FIPSTATE": state,
        "FIPSCTY": county,
        "NAICS": naics,
        "EMP_NF": emp_nf,
        "EMP": emp,
        "QP1_NF": "G",
        "QP1": "7000",
        "AP_NF": ap_nf,
        "AP": ap,
        "EST": est,
        "N<5": "20",
    }


def census_zip(tmp_path, rows, name="cbp24co.zip", member="cbp24co.txt", drop=()):
    df = pd.DataFrame(rows).drop(columns=list(drop))
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(member, df.to_csv(index=False))
    return path


# ---------------------------------------------------------------- reading the file


def test_only_the_rows_the_region_needs_are_kept(cfg, tmp_path):
    rows = [
        census_row(county="003"),  # Maryland county, all industries: keep
        census_row(county="003", naics="23----"),  # a sector: keep
        census_row(county="003", naics="31----"),  # manufacturing, which QCEW writes as 31-33: keep
        census_row(county="999"),  # the statewide row in the county file: drop
        census_row(county="003", naics="236///"),  # a deeper industry level: drop
        census_row(county="003", naics="236220"),  # a six-digit industry: drop
        census_row(state="48", county="201"),  # Texas: drop
        census_row(county="003", naics="99----"),  # a sector QCEW has no match for: drop, and say so
        census_row(county="003", naics="32----"),  # another member of a group: drop, and say so
    ]
    kept, stats = cbp.read_cbp_year(census_zip(tmp_path, rows), cfg)
    assert sorted(kept["NAICS"]) == ["------", "23----", "31----"]
    assert stats == {"rows_scanned": 9, "rows_kept": 3, "ignored_sector_codes": ["32", "99"]}


def test_a_file_with_nothing_for_these_states_gives_an_empty_table(cfg, tmp_path):
    kept, stats = cbp.read_cbp_year(census_zip(tmp_path, [census_row(state="48", county="201")]), cfg)
    assert kept.empty and stats["rows_kept"] == 0


def test_a_changed_layout_fails_loudly(cfg, tmp_path):
    path = census_zip(tmp_path, [census_row()], drop=["EMP"])
    with pytest.raises(ValueError, match="missing expected columns"):
        cbp.read_cbp_year(path, cfg)


def test_a_zip_with_no_data_file_fails_loudly(cfg, tmp_path):
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("readme.pdf", "nothing useful")
    with pytest.raises(ValueError, match="no .txt or .csv"):
        cbp.read_cbp_year(path, cfg)


def test_codes_stay_as_text_so_leading_zeros_survive(tmp_path):
    from qcew_pipeline.config import load_config

    cfg = load_config(region="california")
    kept, _ = cbp.read_cbp_year(census_zip(tmp_path, [census_row(state="06", county="037")]), cfg)
    assert cbp.standardize_cbp(kept, 2024)["area_fips"].tolist() == ["06037"]


# ---------------------------------------------------------------- turning it into clean numbers


def standardized(cfg, tmp_path, rows, **zip_options):
    raw, _ = cbp.read_cbp_year(census_zip(tmp_path, rows, **zip_options), cfg)
    return cbp.standardize_cbp(raw, 2024)


def test_codes_are_translated_to_the_ones_qcew_uses(cfg, tmp_path):
    df = standardized(cfg, tmp_path, [census_row(), census_row(naics="23----"), census_row(naics="31----")])
    assert df["industry_code"].tolist() == ["10", "23", "31-33"]
    assert df["naics_code"].tolist() == ["------", "23----", "31----"]
    assert df["area_fips"].tolist() == ["24003"] * 3


def test_payroll_is_turned_from_thousands_into_dollars(cfg, tmp_path):
    df = standardized(cfg, tmp_path, [census_row(ap="30000")])
    assert df["annual_payroll"].iloc[0] == 30_000_000


def test_noise_flags_are_kept_next_to_the_numbers(cfg, tmp_path):
    df = standardized(cfg, tmp_path, [census_row(emp_nf="J"), census_row(county="005", emp_nf="H", ap_nf="G")])
    assert df["employment_flag"].tolist() == ["J", "H"]
    assert df["payroll_flag"].tolist() == ["G", "G"]
    assert df["is_withheld"].tolist() == [0, 0]


@pytest.mark.parametrize("flag", ["D", "S"])
def test_a_withheld_number_stays_empty_and_never_becomes_zero(cfg, tmp_path, flag):
    df = standardized(cfg, tmp_path, [census_row(emp="0", emp_nf=flag, ap="0", ap_nf=flag)])
    row = df.iloc[0]
    assert pd.isna(row["employment"]) and pd.isna(row["annual_payroll"])
    assert row["is_withheld"] == 1 and row["employment_flag"] == flag


def test_one_withheld_number_does_not_hide_the_other(cfg, tmp_path):
    """Employment can be withheld while payroll is published, and the other way round."""
    df = standardized(cfg, tmp_path, [census_row(emp="0", emp_nf="D", ap="30000", ap_nf="G")])
    assert pd.isna(df["employment"].iloc[0]) and df["annual_payroll"].iloc[0] == 30_000_000


def test_an_older_layout_with_a_size_class_flag_is_treated_as_withheld(cfg, tmp_path):
    row = {**census_row(emp="0"), "EMPFLAG": "B"}
    df = standardized(cfg, tmp_path, [row], drop=["EMP_NF", "AP_NF"])
    assert pd.isna(df["employment"].iloc[0]) and df["is_withheld"].iloc[0] == 1
    assert df["employment_flag"].iloc[0] == "B"  # the size class is kept so the information isn't lost


def test_the_optional_flag_columns_can_be_missing(cfg, tmp_path):
    df = standardized(cfg, tmp_path, [census_row()], drop=["EMP_NF", "AP_NF"])
    assert df["employment"].iloc[0] == 500 and df["employment_flag"].iloc[0] is None


# ---------------------------------------------------------------- saving it


INFO = dict(
    year=2024,
    url="http://example/cbp24co.zip",
    local_name="cbp24co.zip",
    retrieved_at="now",
    download_status="test",
    size_bytes=1,
    sha256="abc",
)
STATS = {"rows_scanned": 10, "rows_kept": 3}


def test_saving_a_year_stores_the_rows_and_a_record_of_the_file(cfg, refs, tmp_path):
    conn, _ = fresh_db(cfg, refs)
    df = standardized(cfg, tmp_path, [census_row(), census_row(naics="23----", emp="0", emp_nf="D")])
    assert cbp.load_cbp_year(conn, df, INFO, STATS, cfg) == 2
    assert conn.execute("SELECT COUNT(*) FROM cbp_county").fetchone()[0] == 2
    assert conn.execute("SELECT employment IS NULL FROM cbp_county WHERE naics_code = '23----'").fetchone()[0] == 1
    assert conn.execute("SELECT sha256 FROM cbp_files").fetchone()[0] == "abc"


def test_saving_the_same_year_twice_does_not_create_duplicates(cfg, refs, tmp_path):
    conn, _ = fresh_db(cfg, refs)
    df = standardized(cfg, tmp_path, [census_row(), census_row(naics="23----")])
    cbp.load_cbp_year(conn, df, INFO, STATS, cfg)
    cbp.load_cbp_year(conn, df, INFO, STATS, cfg)
    assert conn.execute("SELECT COUNT(*) FROM cbp_county").fetchone()[0] == 2


def test_a_revised_file_replaces_the_old_numbers(cfg, refs, tmp_path):
    conn, _ = fresh_db(cfg, refs)
    cbp.load_cbp_year(conn, standardized(cfg, tmp_path, [census_row(emp="500")]), INFO, STATS, cfg)
    cbp.load_cbp_year(conn, standardized(cfg, tmp_path, [census_row(emp="520")]), INFO, STATS, cfg)
    assert conn.execute("SELECT employment FROM cbp_county").fetchall() == [(520.0,)]


def test_saving_one_region_leaves_other_states_alone(cfg, refs, tmp_path):
    conn, _ = fresh_db(cfg, refs)
    conn.execute(
        "INSERT INTO cbp_county (area_fips, year, naics_code, industry_code, is_withheld) "
        "VALUES ('06037', 2024, '------', '10', 0)"
    )
    conn.commit()
    cbp.load_cbp_year(conn, standardized(cfg, tmp_path, [census_row()]), INFO, STATS, cfg)  # Maryland
    assert conn.execute("SELECT COUNT(*) FROM cbp_county WHERE area_fips = '06037'").fetchone()[0] == 1


def test_two_rows_for_the_same_county_and_industry_are_refused(cfg, refs, tmp_path):
    conn, _ = fresh_db(cfg, refs)
    df = standardized(cfg, tmp_path, [census_row(), census_row()])
    with pytest.raises(ValueError, match="more than one row"):
        cbp.load_cbp_year(conn, df, INFO, STATS, cfg)
    assert conn.execute("SELECT COUNT(*) FROM cbp_county").fetchone()[0] == 0


# ---------------------------------------------------------------- comparing the two sources


@pytest.fixture
def both_sources(cfg, refs, clean_df, tmp_path):
    """The fake QCEW data loaded, and County Business Patterns rows that match it exactly (so far)."""
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)

    def add(factor=None, withhold=(), years=(2023, 2024)):
        """Save CBP rows for every QCEW county row. `factor` maps an area to how much bigger CBP's number is."""
        factor = factor or {}
        for year in years:
            q = clean_df[(clean_df["year"] == year) & clean_df["agglvl_code"].isin(["71", "74"])]
            df = pd.DataFrame(
                {
                    "area_fips": q["area_fips"],
                    "year": year,
                    "naics_code": q["industry_code"].map(lambda c: "------" if c == "10" else f"{c}----"),
                    "industry_code": q["industry_code"],
                    "employment": [
                        factor.get(area, 1.0) * jobs for area, jobs in zip(q["area_fips"], q["employment"], strict=True)
                    ],
                    "employment_flag": "G",
                    "annual_payroll": 1.0,
                    "payroll_flag": "G",
                    "establishments": [int(e) for e in q["establishments"]],
                    "is_withheld": 0,
                }
            )
            for area in withhold:
                df.loc[df["area_fips"] == area, ["employment", "is_withheld"]] = [None, 1]
            info = {**INFO, "year": year}
            cbp.load_cbp_year(conn, df, info, STATS, cfg)
        return conn

    return add


def by_name(results):
    return {r.name: r for r in results}


def test_matching_sources_raise_no_warnings(both_sources, cfg):
    conn = both_sources()
    results = by_name(cross_source.compare(conn, cfg, [2023, 2024]))
    assert all(r.passed for r in results.values()), {n: r.detail for n, r in results.items() if not r.passed}
    assert "have a County Business Patterns row" in results["cbp_rows_matched"].detail


def test_a_state_where_the_sources_disagree_is_flagged(both_sources, cfg):
    conn = both_sources(factor={"24001": 1.5, "24003": 1.5})  # CBP says Maryland has 50% more jobs
    results = by_name(cross_source.compare(conn, cfg, [2023, 2024]))
    check = results["cbp_state_employment_ratio"]
    assert check.passed is False and check.level == "warn"
    assert "2023 state 24: 1.500" in check.detail and "2024 state 24: 1.500" in check.detail
    assert "state 11" not in check.detail and "state 51" not in check.detail  # the others still agree


def test_a_small_difference_is_within_tolerance(both_sources, cfg):
    conn = both_sources(factor={"24001": 1.05, "24003": 1.05})
    assert by_name(cross_source.compare(conn, cfg, [2023, 2024]))["cbp_state_employment_ratio"].passed


def test_one_odd_county_is_flagged_by_name(both_sources, cfg):
    cfg["cbp"]["county_min_employment"] = 50  # the fake counties are tiny
    conn = both_sources(factor={"24003": 2.5})
    check = by_name(cross_source.compare(conn, cfg, [2023, 2024]))["cbp_county_outliers"]
    assert check.passed is False
    assert "Anne Arundel (24003): 2.50" in check.detail


def test_small_counties_are_not_judged_one_by_one(both_sources, cfg):
    conn = both_sources(factor={"24003": 2.5})  # same difference, but the county has only 200 jobs
    assert by_name(cross_source.compare(conn, cfg, [2023, 2024]))["cbp_county_outliers"].passed


def test_withheld_numbers_are_left_out_not_counted_as_zero(both_sources, cfg):
    conn = both_sources(withhold=["24003"])
    results = by_name(cross_source.compare(conn, cfg, [2023, 2024]))
    assert results["cbp_state_employment_ratio"].passed  # Maryland is compared using only the county that has numbers
    assert "Census hides" in results["cbp_withheld_share"].detail


def test_years_census_has_not_published_are_not_compared(both_sources, cfg):
    conn = both_sources(years=(2023,))
    results = by_name(cross_source.compare(conn, cfg, [2023]))
    assert "2023 state 24" in results["cbp_state_employment_ratio"].detail
    assert "2024" not in results["cbp_state_employment_ratio"].detail


def test_with_nothing_to_compare_the_check_says_so(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)  # QCEW only, no CBP rows at all
    check = by_name(cross_source.compare(conn, cfg, [2023]))["cbp_state_employment_ratio"]
    assert check.passed is False and "No all-industry county row" in check.detail


def test_results_are_kept_in_the_database_and_in_a_report(both_sources, cfg):
    conn = both_sources(factor={"24001": 1.5, "24003": 1.5})
    results = cross_source.compare(conn, cfg, [2023, 2024])
    cross_source.store_results(conn, results)
    assert conn.execute("SELECT COUNT(*) FROM cross_source_results").fetchone()[0] == len(results)
    report = cross_source.write_report(cfg, results, [2023, 2024], [2025], ["99"])
    text = report.read_text(encoding="utf-8")
    assert "WARN" in text and "cbp_state_employment_ratio" in text
    assert "[2025]" in text and "['99']" in text


def test_the_view_only_pairs_private_sector_county_rows(both_sources):
    conn = both_sources()
    states = {r[0] for r in conn.execute("SELECT DISTINCT state_fips FROM v_qcew_vs_cbp")}
    assert states == {"11", "24", "51"}  # state-total rows (area codes ending 000) never appear
    assert conn.execute("SELECT COUNT(*) FROM v_qcew_vs_cbp WHERE area_fips LIKE '%000'").fetchone()[0] == 0


# ---------------------------------------------------------------- the command


@pytest.fixture
def command(monkeypatch, cfg, refs, clean_df, tmp_path):
    """The real command, with the network replaced by a made-up Census file for each published year."""
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    conn.close()
    published = {2023, 2024}
    asked = []

    def fake_download(year, config):
        asked.append(year)
        rows = [
            census_row(state=a[:2], county=a[2:], emp=str(int(e)), est=str(int(s)))
            for a, e, s in clean_df[(clean_df["year"] == year) & (clean_df["agglvl_code"] == "71")][
                ["area_fips", "employment", "establishments"]
            ].itertuples(index=False)
        ]
        path = census_zip(tmp_path, rows, name=f"cbp{year % 100}co.zip")
        return {**INFO, "year": year, "local_path": str(path), "local_name": path.name}

    monkeypatch.setattr(cbp, "cbp_published", lambda year, config: year in published)
    monkeypatch.setattr(cbp, "download_cbp_file", fake_download)
    monkeypatch.setattr(cbp, "load_config", lambda path=None, region=None: cfg)
    return published, asked


def test_the_command_downloads_loads_and_compares_each_published_year(command, cfg):
    _, asked = command
    result = cbp.run_cbp(cfg)
    assert asked == [2023, 2024] and result["loaded_years"] == [2023, 2024]
    assert result["not_published"] == []
    assert result["rows_saved"] == {2023: 4, 2024: 4}  # four county totals a year in the fake data
    assert all(r["passed"] for r in result["results"] if r["level"] == "warn")


def test_years_census_has_not_published_yet_are_skipped_and_reported(command, cfg):
    published, asked = command
    published.discard(2024)
    result = cbp.run_cbp(cfg)
    assert result["loaded_years"] == [2023] and result["not_published"] == [2024] and asked == [2023]


def test_nothing_published_yet_is_a_clear_message_not_a_crash(command, cfg):
    published, _ = command
    published.clear()
    with pytest.raises(cbp.CbpError, match="hasn't published"):
        cbp.run_cbp(cfg)


def test_an_empty_database_says_to_run_the_pipeline_first(cfg):
    with pytest.raises(cbp.CbpError, match="Run qcew-run first"):
        cbp.run_cbp(cfg)


def test_the_years_flag_limits_which_years_are_compared(command, cfg):
    _, asked = command
    result = cbp.run_cbp(cfg, [2024])
    assert asked == [2024] and result["loaded_years"] == [2024]


def test_asking_for_a_year_that_is_not_in_the_database_is_refused(command, cfg):
    with pytest.raises(cbp.CbpError, match="None of the years"):
        cbp.run_cbp(cfg, [1999])


def test_the_command_exits_zero_and_prints_where_the_report_is(command, capsys):
    assert cbp.main([]) == 0
    out = capsys.readouterr().out
    assert "Compared QCEW with County Business Patterns for 2023-2024" in out and "cbp_comparison.md" in out


def test_the_command_exits_two_when_there_is_nothing_to_compare(command, capsys):
    published, _ = command
    published.clear()
    assert cbp.main([]) == 2
    assert "hasn't published" in capsys.readouterr().err


def test_the_command_exits_one_when_census_cannot_be_reached(command, monkeypatch, capsys):
    def unreachable(year, config):
        raise CheckError("Could not reach census.gov")

    monkeypatch.setattr(cbp, "cbp_published", unreachable)
    assert cbp.main([]) == 1
    assert "Could not reach census.gov" in capsys.readouterr().err


def test_a_failed_download_exits_one(command, monkeypatch, capsys):
    def broken(year, config):
        raise collect.DownloadError("Could not download cbp24co.zip")

    monkeypatch.setattr(cbp, "download_cbp_file", broken)
    assert cbp.main([]) == 1
    assert "Could not download" in capsys.readouterr().err
