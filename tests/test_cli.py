"""Tests for the command line: exit codes, the summary file, and the optional export."""

import json

import pytest

from qcew_pipeline import export, run
from qcew_pipeline.years import CheckError


@pytest.fixture
def cli(monkeypatch, cfg):
    """Replace the real pipeline with a stub, and record what the command line asked of it."""

    def make(status):
        seen = {"years": None, "exported": False}

        def fake_run(config, years=None, refs=None):
            seen["years"] = years
            return {"status": status, "years": years}

        monkeypatch.setattr(run, "load_config", lambda path=None: cfg)
        monkeypatch.setattr(run, "run_pipeline", fake_run)
        monkeypatch.setattr(export, "export_all", lambda c: seen.__setitem__("exported", True))
        return seen

    return make


def test_a_successful_run_exits_zero_and_writes_a_summary(cli, cfg):
    seen = cli("success")
    assert run.main([]) == 0
    assert seen["years"] == [2023, 2024]  # the fixed list from the config
    summaries = list(cfg["paths"]["output_dir"].glob("run_summary_*.json"))
    assert len(summaries) == 1 and json.loads(summaries[0].read_text())["status"] == "success"


@pytest.mark.parametrize("status", ["failed", "failed_validation"])
def test_a_failed_run_exits_one(cli, status):
    cli(status)
    assert run.main([]) == 1


def test_the_years_flag_overrides_the_config(cli):
    seen = cli("success")
    run.main(["--years", "2020", "2021"])
    assert seen["years"] == [2020, 2021]


def test_the_export_only_happens_on_success_and_when_asked(cli):
    seen = cli("success")
    run.main([])
    assert seen["exported"] is False
    run.main(["--export"])
    assert seen["exported"] is True

    seen = cli("failed")
    run.main(["--export"])
    assert seen["exported"] is False


def test_if_the_newest_year_cannot_be_worked_out_it_exits_two_without_running(cli, cfg, monkeypatch):
    seen = cli("success")
    cfg["years"], cfg["window_years"] = "auto", 5

    def unreachable(config):
        raise CheckError("BLS unreachable")

    monkeypatch.setattr(run, "resolve_years", unreachable)

    assert run.main([]) == 2
    assert seen["years"] is None  # the pipeline never started
