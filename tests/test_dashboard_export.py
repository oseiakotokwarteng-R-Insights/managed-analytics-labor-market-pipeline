"""Tests for the files the dashboard reads: the right numbers, hidden values left empty, and nothing half-written."""

import json

import pytest

from qcew_pipeline import dashboard_export as de
from qcew_pipeline import load
from qcew_pipeline.validate import CheckResult
from tests.test_load import fake_sources, fresh_db


def read(folder, name):
    def refuse(constant):
        raise AssertionError(f"{name} contains {constant}, which isn't valid JSON")

    return json.loads((folder / name).read_text(encoding="utf-8"), parse_constant=refuse)


@pytest.fixture
def loaded(cfg, refs, clean_df):
    """A database with the fake QCEW data loaded, one finished run, and its validation results."""
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    load.record_validation(
        conn,
        run_id,
        [
            CheckResult("no_duplicate_keys", "error", True, 0, "ok"),
            CheckResult("suppression_rate_stable", "warn", False, 1, "2024 has 9 points more hidden values"),
        ],
    )
    load.finish_run(conn, run_id, "success", rows_inserted=34, validation_errors=0, validation_warnings=1)
    return conn


def export(cfg, tmp_path):
    out = tmp_path / "data"
    summary = de.export_dashboard(cfg, out)
    return out, summary


# ---------------------------------------------------------------- the files


def test_every_file_the_dashboard_needs_is_written(loaded, cfg, tmp_path):
    out, summary = export(cfg, tmp_path)
    assert {p.name for p in out.iterdir()} == {"meta.json", "counties.json", "states.json", "checks.json", "sectors"}
    assert sorted(p.name for p in (out / "sectors").iterdir()) == [
        "11001.json",
        "24001.json",
        "24003.json",
        "51013.json",
    ]
    assert summary["years"] == [2023, 2024] and summary["counties"] == 4 and summary["has_cbp"] is False


def test_the_meta_file_says_what_the_data_is(loaded, cfg, tmp_path):
    out, _ = export(cfg, tmp_path)
    meta = read(out, "meta.json")
    assert meta["region"] == "dmv" and meta["years"] == [2023, 2024] and meta["latest_year"] == 2024
    assert [s["fips"] for s in meta["states"]] == ["11", "24", "51"]
    assert meta["states"][1]["name"] == "Maryland"
    assert meta["last_run"]["status"] == "success" and meta["last_run"]["rows_inserted"] == 34
    assert [s["year"] for s in meta["sources"]] == [2023, 2024] and meta["sources"][0]["sha256"] == "abc"
    assert meta["has_cbp"] is False and meta["generated_at"].endswith("+00:00")


def test_counties_have_one_number_per_year_in_year_order(loaded, cfg, tmp_path):
    out, _ = export(cfg, tmp_path)
    data = read(out, "counties.json")
    assert data["years"] == [2023, 2024]
    by_fips = {c["fips"]: c for c in data["counties"]}
    assert sorted(by_fips) == ["11001", "24001", "24003", "51013"]
    anne_arundel = by_fips["24003"]
    assert anne_arundel["title"] == "Anne Arundel" and anne_arundel["state"] == "24"
    assert anne_arundel["employment"] == [200, 200] and anne_arundel["wage"] == [1000, 1000]
    assert anne_arundel["establishments"] == [10, 10] and anne_arundel["hidden"] == [0, 0]


def test_the_sector_files_hold_each_countys_sectors(loaded, cfg, tmp_path):
    out, _ = export(cfg, tmp_path)
    data = read(out, "sectors/24003.json")
    assert data["title"] == "Anne Arundel" and data["years"] == [2023, 2024]
    sectors = {s["code"]: s for s in data["sectors"]}
    assert sorted(sectors) == ["23", "62"]
    assert sectors["62"]["title"] == "Health care" and sectors["62"]["employment"] == [150, 150]


def test_the_checks_file_has_the_last_runs_results_and_the_history(loaded, cfg, tmp_path):
    out, _ = export(cfg, tmp_path)
    data = read(out, "checks.json")
    names = {c["name"]: c for c in data["checks"]}
    assert names["suppression_rate_stable"]["passed"] is False and names["suppression_rate_stable"]["level"] == "warn"
    assert names["no_duplicate_keys"]["passed"] is True
    assert data["history"][0]["status"] == "success" and data["history"][0]["rows_inserted"] == 34


# ---------------------------------------------------------------- names, as BLS really writes them


def test_dc_is_shown_by_its_name_not_by_the_odd_title_bls_uses(loaded, cfg, tmp_path):
    loaded.execute(
        "UPDATE dim_area SET area_title = 'District of Columbia, not unknown' WHERE area_fips IN ('11000', '11001')"
    )
    loaded.commit()
    out, _ = export(cfg, tmp_path)
    assert read(out, "meta.json")["states"][0] == {"fips": "11", "name": "District of Columbia"}
    county = {c["fips"]: c for c in read(out, "counties.json")["counties"]}["11001"]
    assert county["title"] == "District of Columbia"
    assert read(out, "sectors/11001.json")["title"] == "District of Columbia"
    assert next(r for r in read(out, "states.json")["rows"] if r["state"] == "11")["name"] == "District of Columbia"


def test_state_names_come_from_the_code_not_the_title(loaded, cfg, tmp_path):
    loaded.execute("UPDATE dim_area SET area_title = 'Some odd title' WHERE area_fips = '24000'")
    loaded.commit()
    out, _ = export(cfg, tmp_path)
    assert [s["name"] for s in read(out, "meta.json")["states"]] == ["District of Columbia", "Maryland", "Virginia"]


def test_industry_titles_lose_the_code_in_front(loaded, cfg, tmp_path):
    loaded.execute(
        "UPDATE dim_industry SET industry_title = 'NAICS 62 Health care and social assistance' "
        "WHERE industry_code = '62'"
    )
    loaded.execute("UPDATE dim_industry SET industry_title = '23 Construction' WHERE industry_code = '23'")
    loaded.commit()
    out, _ = export(cfg, tmp_path)
    sectors = {s["code"]: s["title"] for s in read(out, "sectors/24003.json")["sectors"]}
    assert sectors == {"23": "Construction", "62": "Health care and social assistance"}


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("NAICS 31-33 Manufacturing", "Manufacturing"),
        ("NAICS 99 Unclassified", "Unclassified"),
        ("44-45 Retail trade", "Retail trade"),
        ("Health care", "Health care"),  # nothing to remove
        ("", ""),
    ],
)
def test_industry_title_cleaning(raw, clean):
    assert de._clean_industry_title(raw) == clean


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("District of Columbia, not unknown", "District of Columbia"),
        ("Montgomery County, Maryland", "Montgomery County, Maryland"),
        ("Maryland -- Statewide", "Maryland -- Statewide"),
    ],
)
def test_area_title_cleaning(raw, clean):
    assert de._clean_area_title(raw) == clean


# ---------------------------------------------------------------- hidden numbers


def test_a_hidden_number_is_null_never_zero(loaded, cfg, tmp_path):
    loaded.execute(
        "UPDATE qcew_annual SET employment = NULL, avg_weekly_wage = NULL, establishments = NULL, is_suppressed = 1, "
        "disclosure_code = 'N' WHERE area_fips = '24003' AND year = 2024 AND agglvl_code = '71'"
    )
    loaded.commit()
    out, _ = export(cfg, tmp_path)
    county = {c["fips"]: c for c in read(out, "counties.json")["counties"]}["24003"]
    assert county["employment"] == [200, None] and county["wage"] == [1000, None]
    assert county["hidden"] == [0, 1]


def test_a_year_a_county_is_missing_is_null_too(loaded, cfg, tmp_path):
    loaded.execute("DELETE FROM qcew_annual WHERE area_fips = '51013' AND year = 2023")
    loaded.commit()
    out, _ = export(cfg, tmp_path)
    county = {c["fips"]: c for c in read(out, "counties.json")["counties"]}["51013"]
    assert county["employment"] == [None, 50] and county["hidden"] == [0, 0]


# ---------------------------------------------------------------- the state table


def state_row(out, fips, year):
    rows = read(out, "states.json")["rows"]
    return next(r for r in rows if r["state"] == fips and r["year"] == year)


def add_unknown_bucket(conn, employment, suppressed=0):
    conn.execute("INSERT INTO dim_area VALUES ('24999', 'Unknown Or Undefined, Maryland', '24', 'undefined')")
    conn.execute(
        "INSERT INTO qcew_annual (area_fips, year, own_code, industry_code, agglvl_code, is_suppressed, employment) "
        "VALUES ('24999', 2024, '5', '10', '71', ?, ?)",
        (suppressed, employment),
    )
    conn.commit()


def test_the_state_total_is_compared_with_its_counties(loaded, cfg, tmp_path):
    out, _ = export(cfg, tmp_path)
    row = state_row(out, "24", 2024)
    assert row["name"] == "Maryland" and row["state_total"] == 300 and row["named_counties"] == 300
    assert row["gap_without_bucket_pct"] == 0 and row["gap_with_bucket_pct"] == 0
    assert row["unknown_bucket"] is None and row["bucket_share_pct"] is None
    assert row["unknown_bucket_hidden"] is False  # there simply isn't a bucket row, which is different from hidden


def test_the_unknown_bucket_is_counted_and_shown_beside_the_counties(loaded, cfg, tmp_path):
    add_unknown_bucket(loaded, 12)
    out, _ = export(cfg, tmp_path)
    row = state_row(out, "24", 2024)
    assert row["unknown_bucket"] == 12 and row["bucket_share_pct"] == 4.0
    assert row["gap_without_bucket_pct"] == 0  # the named counties alone match the state total here
    assert row["gap_with_bucket_pct"] == 4.0  # adding the bucket on top overshoots it, and that shows up


def test_a_hidden_bucket_leaves_only_the_gap_that_needs_it_empty(loaded, cfg, tmp_path):
    add_unknown_bucket(loaded, None, suppressed=1)
    out, _ = export(cfg, tmp_path)
    row = state_row(out, "24", 2024)
    assert row["unknown_bucket"] is None and row["unknown_bucket_hidden"] is True
    assert row["gap_with_bucket_pct"] is None and row["gap_without_bucket_pct"] == 0


def test_a_hidden_county_leaves_the_gap_empty_instead_of_guessing(loaded, cfg, tmp_path):
    loaded.execute(
        "UPDATE qcew_annual SET employment = NULL, is_suppressed = 1 WHERE area_fips = '24001' AND year = 2024 "
        "AND agglvl_code = '71'"
    )
    loaded.commit()
    out, _ = export(cfg, tmp_path)
    row = state_row(out, "24", 2024)
    assert row["counties_hidden"] == 1 and row["gap_without_bucket_pct"] is None and row["gap_with_bucket_pct"] is None
    assert row["state_total"] == 300  # BLS still published the state total


# ---------------------------------------------------------------- the second source


def add_cbp(conn):
    rows = [("24001", 2023, 110.0), ("24003", 2023, 220.0), ("11001", 2023, 100.0)]
    for fips, year, employment in rows:
        conn.execute(
            "INSERT INTO cbp_county (area_fips, year, naics_code, industry_code, employment, is_withheld) "
            "VALUES (?, ?, '------', '10', ?, 0)",
            (fips, year, employment),
        )
    conn.execute(
        "INSERT INTO cross_source_results (compared_at, check_name, level, passed, failed_count, detail) "
        "VALUES ('2026-01-01T00:00:00+00:00', 'cbp_state_employment_ratio', 'warn', 1, 0, 'close')"
    )
    conn.commit()


def test_the_comparison_with_county_business_patterns_is_included_when_it_has_been_run(loaded, cfg, tmp_path):
    add_cbp(loaded)
    out, summary = export(cfg, tmp_path)
    assert summary["has_cbp"] is True and read(out, "meta.json")["has_cbp"] is True
    data = read(out, "cbp.json")
    assert data["results"][0]["name"] == "cbp_state_employment_ratio" and data["results"][0]["passed"] is True
    maryland = next(r for r in data["state_ratios"] if r["state"] == "24")
    assert maryland["year"] == 2023 and maryland["ratio"] == 1.1


# ---------------------------------------------------------------- not showing half of an update


def test_an_existing_folder_is_replaced_in_one_step(loaded, cfg, tmp_path):
    out = tmp_path / "data"
    out.mkdir()
    (out / "old_file.json").write_text("{}", encoding="utf-8")
    de.export_dashboard(cfg, out)
    assert not (out / "old_file.json").exists() and (out / "meta.json").exists()
    assert not (tmp_path / "data.new").exists() and not (tmp_path / "data.old").exists()


def test_a_failed_export_leaves_the_old_files_in_place(loaded, cfg, tmp_path, monkeypatch):
    out, _ = export(cfg, tmp_path)
    before = (out / "meta.json").read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(de, "_checks", boom)
    with pytest.raises(RuntimeError):
        de.export_dashboard(cfg, out)
    assert (out / "meta.json").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------- nothing to show yet


def test_no_database_says_to_run_the_pipeline_first(cfg, tmp_path):
    with pytest.raises(de.DashboardError, match="Run qcew-run first"):
        de.export_dashboard(cfg, tmp_path / "data")


def test_an_empty_database_says_to_run_the_pipeline_first(cfg, refs, tmp_path):
    conn, _ = fresh_db(cfg, refs)
    conn.close()
    with pytest.raises(de.DashboardError, match="no private-sector rows"):
        de.export_dashboard(cfg, tmp_path / "data")


# ---------------------------------------------------------------- the command


def test_the_command_writes_the_files_and_says_where(loaded, cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(de, "load_config", lambda path=None, region=None: cfg)
    assert de.main(["--out", str(tmp_path / "site-data")]) == 0
    out = capsys.readouterr().out
    assert "4 counties" in out and "2023-2024" in out and "site-data" in out
    assert (tmp_path / "site-data" / "meta.json").is_file()


def test_the_command_exits_two_when_there_is_nothing_to_show(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(de, "load_config", lambda path=None, region=None: cfg)
    assert de.main(["--out", str(tmp_path / "x")]) == 2
    assert "Run qcew-run first" in capsys.readouterr().err
