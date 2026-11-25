"""Tests for regions: DC/Maryland/Virginia stays the default, and California works by changing a setting."""

import pandas as pd
import pytest

from qcew_pipeline import transform, validate
from qcew_pipeline.config import DEFAULT_CONFIG, load_config, load_regions
from tests.conftest import make_row
from tests.test_read_year import make_zip, raw_row


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("QCEW_REGION", "QCEW_DB_PATH", "QCEW_LAKE_DIR", "QCEW_ANALYTICS_DB"):
        monkeypatch.delenv(name, raising=False)


def settings_file(tmp_path, *swaps):
    """A copy of the shipped settings with some text swapped for other text."""
    text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    for old, new in swaps:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / "mine.toml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- the regions themselves


def test_dc_maryland_and_virginia_is_still_the_default():
    cfg = load_config()
    assert cfg["region"] == "dmv"
    assert cfg["state_fips"] == ["11", "24", "51"]
    assert cfg["validation"]["expected_county_counts"] == {"11": 1, "24": 24, "51": 133}
    assert cfg["paths"]["db_path"].name == "qcew_dmv.sqlite"
    assert cfg["paths"]["lake_dir"].name == "lake"
    assert cfg["paths"]["analytics_db"].name == "analytics.duckdb"


def test_california_is_one_setting_away():
    cfg = load_config(region="california")
    assert cfg["region"] == "california" and cfg["region_name"] == "California"
    assert cfg["state_fips"] == ["06"]
    assert cfg["validation"]["expected_county_counts"] == {"06": 58}


def test_every_region_has_everything_the_pipeline_needs():
    regions = load_regions()
    assert {"dmv", "california"} <= set(regions)
    for name, region in regions.items():
        for key in ("name", "state_fips", "expected_county_counts", "db_path", "lake_dir", "analytics_db"):
            assert key in region, f"{name} is missing {key}"
        assert set(region["expected_county_counts"]) <= set(region["state_fips"]), name


def test_regions_never_share_a_database_or_a_folder():
    for key in ("db_path", "lake_dir", "analytics_db"):
        values = [region[key] for region in load_regions().values()]
        assert len(values) == len(set(values)), f"two regions share {key}"


def test_the_other_settings_do_not_change_between_regions():
    dmv, california = load_config(), load_config(region="california")
    for key in ("years", "window_years", "ownership_codes", "agglvl_codes", "size_code", "urls"):
        assert dmv[key] == california[key]
    assert dmv["validation"]["min_rows_per_year"] == california["validation"]["min_rows_per_year"]
    assert dmv["paths"]["raw_dir"] == california["paths"]["raw_dir"]  # the national downloads are shared


# ---------------------------------------------------------------- how a region gets picked


def test_the_environment_variable_picks_the_region(monkeypatch):
    monkeypatch.setenv("QCEW_REGION", "california")
    assert load_config()["region"] == "california"


def test_the_settings_file_can_pick_the_region(tmp_path):
    path = settings_file(tmp_path, ('region = "dmv"', 'region = "california"'))
    assert load_config(path)["region"] == "california"


def test_the_argument_beats_the_environment_variable(monkeypatch):
    monkeypatch.setenv("QCEW_REGION", "california")
    assert load_config(region="dmv")["region"] == "dmv"


def test_an_unknown_region_is_refused_and_the_choices_are_listed():
    with pytest.raises(ValueError, match="Unknown region 'texas'.*california, dmv"):
        load_config(region="texas")


# ---------------------------------------------------------------- your own settings still win


def test_states_in_the_settings_file_win_over_the_region(tmp_path):
    path = settings_file(tmp_path, ('region = "dmv"', 'region = "dmv"\nstate_fips = ["06", "41"]'))
    cfg = load_config(path, region="california")
    assert cfg["state_fips"] == ["06", "41"]
    assert cfg["validation"]["expected_county_counts"] == {"06": 58}  # not set in the file, so the region fills it in


def test_a_settings_file_that_sets_everything_itself_keeps_working(tmp_path):
    """A settings file written before regions existed names its own states, counts and files."""
    path = settings_file(
        tmp_path,
        ('region = "dmv"', 'state_fips = ["24"]'),
        ("[paths]\n", '[paths]\ndb_path = "mine.sqlite"\nlake_dir = "mine_lake"\nanalytics_db = "mine.duckdb"\n'),
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write('\n[validation.expected_county_counts]\n"24" = 24\n')
    cfg = load_config(path)
    assert cfg["state_fips"] == ["24"]
    assert cfg["validation"]["expected_county_counts"] == {"24": 24}
    assert cfg["paths"]["db_path"].name == "mine.sqlite" and cfg["paths"]["lake_dir"].name == "mine_lake"


def test_environment_paths_still_beat_the_region(monkeypatch, tmp_path):
    monkeypatch.setenv("QCEW_DB_PATH", str(tmp_path / "mine.sqlite"))
    assert load_config(region="california")["paths"]["db_path"] == tmp_path / "mine.sqlite"


# ---------------------------------------------------------------- the pipeline really follows the region


def test_a_california_run_keeps_only_california_rows(tmp_path):
    cfg = load_config(region="california")
    rows = [
        raw_row("06037"),  # Los Angeles County total: keep
        raw_row("06000", agglvl="51"),  # California state total: keep
        raw_row("06999"),  # the "unknown or undefined" bucket: keep, the pipeline counts it
        raw_row("06075", agglvl="74", industry="62"),  # San Francisco health care: keep
        raw_row("11001"),  # DC: drop, it isn't in this region
        raw_row("51013"),  # Virginia: drop
        raw_row("48201"),  # Texas: drop
    ]
    kept, stats = transform.read_year(make_zip(tmp_path, rows), cfg)
    assert stats == {"rows_scanned": 7, "rows_kept": 4}
    assert sorted(kept["area_fips"]) == ["06000", "06037", "06075", "06999"]


def test_the_county_count_check_uses_the_regions_numbers():
    cfg = load_config(region="california")
    df = pd.DataFrame(
        [make_row("06001", 2024, "71", "10", 100), make_row("06003", 2024, "71", "10", 100)]  # 2 counties, not 58
    )
    checks = {c.name: c for c in validate._coverage_checks(df, cfg)}
    assert checks["expected_county_counts_each_year"].passed is False
    assert "(2024, '06', 2, 58)" in checks["expected_county_counts_each_year"].detail


def test_the_dmv_county_numbers_are_not_applied_to_california():
    cfg = load_config(region="california")
    assert "24" not in cfg["validation"]["expected_county_counts"]
    assert cfg["validation"]["expected_county_counts"].keys() == {"06"}
