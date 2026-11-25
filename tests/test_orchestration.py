"""Tests for the Dagster layer: it's wired up the way I meant, and each piece reports honestly.

Nothing here downloads anything. The real work is replaced with small stand-ins, so these tests are
only about what the Dagster layer does with the answers.
"""

import pytest

dg = pytest.importorskip("dagster")

from qcew_pipeline import analytics, load, orchestration, years  # noqa: E402
from tests.test_load import fake_sources, fresh_db  # noqa: E402


@pytest.fixture
def wired(cfg, monkeypatch):
    """Point the Dagster layer at the test settings."""
    monkeypatch.setattr(orchestration, "_config", lambda: cfg)
    return cfg


def check(name, level="info", passed=True, detail="fine"):
    return {"name": name, "level": level, "passed": passed, "failed_count": 0 if passed else 1, "detail": detail}


def good_result(checks=None):
    return {
        "status": "success",
        "run_id": 7,
        "years": [2021, 2022, 2023, 2024, 2025],
        "rows_loaded": 100,
        "rows_scanned": 5000,
        "load": {"inserted": 90, "changed": 4, "unchanged": 5, "removed": 1},
        "analytic_hash": "abc123",
        "timings_seconds": {"total": 1.5},
        "checks": checks or [check("rows_present")],
    }


# ---------------------------------------------------------------- how it's wired


def test_the_assets_run_in_order():
    key = dg.AssetKey
    assert orchestration.qcew_parquet_files.asset_deps[key("qcew_parquet_files")] == {key("qcew_database")}
    assert orchestration.qcew_analytics_tables.asset_deps[key("qcew_analytics_tables")] == {key("qcew_parquet_files")}


def test_the_job_schedule_and_sensor_are_registered():
    defs = orchestration.defs
    assert orchestration.qcew_refresh in defs.jobs
    assert orchestration.monthly_refresh in defs.schedules
    assert orchestration.new_bls_year_sensor in defs.sensors


def test_the_schedule_and_sensor_timing():
    assert orchestration.monthly_refresh.cron_schedule == "0 14 1 * *"
    assert orchestration.new_bls_year_sensor.minimum_interval_seconds == 24 * 60 * 60


def test_nothing_starts_until_it_is_switched_on():
    assert orchestration.monthly_refresh.default_status == dg.DefaultScheduleStatus.STOPPED
    assert orchestration.new_bls_year_sensor.default_status == dg.DefaultSensorStatus.STOPPED


# ---------------------------------------------------------------- the database asset


def test_a_good_run_reports_what_it_did(wired, monkeypatch):
    monkeypatch.setattr(orchestration, "run_pipeline", lambda cfg: good_result())
    result = dg.materialize([orchestration.qcew_database])
    assert result.success
    metadata = result.get_asset_materialization_events()[0].materialization.metadata
    assert metadata["run_id"].value == 7
    assert metadata["rows_inserted"].value == 90
    assert metadata["rows_removed"].value == 1
    assert metadata["years"].value == "2021-2025"
    evaluations = result.get_asset_check_evaluations()
    assert [(e.check_name, e.passed) for e in evaluations] == [("no_validation_warnings", True)]


def test_warnings_are_flagged_but_do_not_fail_the_run(wired, monkeypatch):
    drift = check("hidden_share_drift", level="warn", passed=False, detail="2025 jumped 12 points")
    monkeypatch.setattr(orchestration, "run_pipeline", lambda cfg: good_result([check("rows_present"), drift]))
    result = dg.materialize([orchestration.qcew_database])
    assert result.success
    (evaluation,) = result.get_asset_check_evaluations()
    assert evaluation.passed is False
    assert evaluation.severity == dg.AssetCheckSeverity.WARN
    assert "hidden_share_drift" in evaluation.metadata["warnings"].value


def test_a_failed_validation_fails_the_asset(wired, monkeypatch):
    bad = {
        "status": "failed_validation",
        "run_id": 8,
        "checks": [check("no_duplicates", level="error", passed=False, detail="3 duplicate keys")],
    }
    monkeypatch.setattr(orchestration, "run_pipeline", lambda cfg: bad)
    result = dg.materialize([orchestration.qcew_database], raise_on_error=False)
    assert not result.success


def test_a_failed_download_fails_the_asset(wired, monkeypatch):
    broken = {"status": "failed", "run_id": 9, "error": "Could not download 2025"}
    monkeypatch.setattr(orchestration, "run_pipeline", lambda cfg: broken)
    result = dg.materialize([orchestration.qcew_database], raise_on_error=False)
    assert not result.success


def test_not_reaching_bls_fails_the_asset(wired, monkeypatch):
    def cannot_reach(cfg):
        raise years.CheckError("BLS didn't answer")

    monkeypatch.setattr(orchestration, "run_pipeline", cannot_reach)
    result = dg.materialize([orchestration.qcew_database], raise_on_error=False)
    assert not result.success


# ---------------------------------------------------------------- the Parquet and analytics assets


def test_publishing_reports_rows_and_years(wired, monkeypatch):
    manifest = {
        "analytic_hash": "abc123",
        "tables": {"qcew_annual": {"rows": 3, "partitions": {"2023": {"rows": 1}, "2024": {"rows": 2}}}},
    }
    monkeypatch.setattr(orchestration, "publish_lake", lambda cfg: manifest)
    result = dg.materialize([orchestration.qcew_parquet_files])
    assert result.success
    metadata = result.get_asset_materialization_events()[0].materialization.metadata
    assert metadata["rows"].value == 3
    assert metadata["years"].value == "2023-2024"


def test_publishing_an_empty_database_fails_the_asset(wired, monkeypatch):
    def nothing_to_publish(cfg):
        raise ValueError("The database has no data to publish.")

    monkeypatch.setattr(orchestration, "publish_lake", nothing_to_publish)
    result = dg.materialize([orchestration.qcew_parquet_files], raise_on_error=False)
    assert not result.success


def test_a_passing_dbt_build_succeeds(wired, monkeypatch):
    monkeypatch.setattr(orchestration, "build_analytics", lambda cfg: True)
    assert dg.materialize([orchestration.qcew_analytics_tables]).success


def test_failing_dbt_tests_fail_the_asset(wired, monkeypatch):
    monkeypatch.setattr(orchestration, "build_analytics", lambda cfg: False)
    assert not dg.materialize([orchestration.qcew_analytics_tables], raise_on_error=False).success


def test_a_dbt_build_that_cannot_start_fails_the_asset(wired, monkeypatch):
    def cannot_start(cfg):
        raise analytics.AnalyticsError("The analytics database is open in another program.")

    monkeypatch.setattr(orchestration, "build_analytics", cannot_start)
    assert not dg.materialize([orchestration.qcew_analytics_tables], raise_on_error=False).success


# ---------------------------------------------------------------- the cross-check


class FakeProcess:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_the_cross_check_passes_when_everything_matches(monkeypatch):
    monkeypatch.setattr(orchestration.subprocess, "run", lambda *a, **k: FakeProcess(0, "all 5 comparisons match"))
    result = orchestration.database_pandas_dbt_agree()
    assert result.passed is True
    assert "all 5 comparisons match" in result.metadata["output"].value


@pytest.mark.parametrize("code", [1, 2])
def test_the_cross_check_fails_on_a_mismatch_or_when_it_cannot_run(monkeypatch, code):
    monkeypatch.setattr(orchestration.subprocess, "run", lambda *a, **k: FakeProcess(code, "", "something is off"))
    assert orchestration.database_pandas_dbt_agree().passed is False


def test_the_cross_check_fails_if_the_script_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestration, "CROSS_CHECK_SCRIPT", tmp_path / "missing.py")
    assert orchestration.database_pandas_dbt_agree().passed is False


# ---------------------------------------------------------------- the sensor


def sense(monkeypatch, latest, loaded):
    monkeypatch.setattr(orchestration, "_config", lambda: {})
    monkeypatch.setattr(years, "latest_published_year", lambda cfg: latest)
    monkeypatch.setattr(orchestration, "newest_loaded_year", lambda cfg: loaded)
    return orchestration.new_bls_year_sensor(dg.build_sensor_context())


def test_the_sensor_asks_for_a_run_when_bls_has_a_new_year(monkeypatch):
    request = sense(monkeypatch, latest=2026, loaded=2025)
    assert isinstance(request, dg.RunRequest)
    assert request.run_key == "qcew-2026"


def test_the_sensor_stays_quiet_when_nothing_is_new(monkeypatch):
    assert isinstance(sense(monkeypatch, latest=2025, loaded=2025), dg.SkipReason)


def test_the_sensor_asks_for_a_run_when_the_database_is_empty(monkeypatch):
    assert isinstance(sense(monkeypatch, latest=2025, loaded=None), dg.RunRequest)


def test_the_sensor_fails_loudly_when_bls_cannot_be_reached(monkeypatch):
    def cannot_reach(cfg):
        raise years.CheckError("BLS didn't answer")

    monkeypatch.setattr(orchestration, "_config", lambda: {})
    monkeypatch.setattr(years, "latest_published_year", cannot_reach)
    with pytest.raises(years.CheckError):
        orchestration.new_bls_year_sensor(dg.build_sensor_context())


# ---------------------------------------------------------------- the newest loaded year


def test_no_database_means_no_loaded_year(cfg):
    assert orchestration.newest_loaded_year(cfg) is None


def test_a_database_without_tables_means_no_loaded_year(cfg):
    load.connect(cfg["paths"]["db_path"]).close()
    assert orchestration.newest_loaded_year(cfg) is None


def test_an_empty_table_means_no_loaded_year(cfg, refs):
    conn, _ = fresh_db(cfg, refs)
    conn.close()
    assert orchestration.newest_loaded_year(cfg) is None


def test_the_newest_loaded_year_comes_from_the_data(cfg, refs, clean_df):
    conn, run_id = fresh_db(cfg, refs)
    load.load_facts(conn, clean_df, run_id, fake_sources(conn, run_id), cfg)
    conn.close()
    assert orchestration.newest_loaded_year(cfg) == 2024
