"""Tests for the new-data check. BLS is never contacted: the web request is replaced."""

import urllib.error

import pytest

from qcew_pipeline import check_updates


def test_it_asks_about_the_year_after_the_latest_configured_one(cfg, monkeypatch):
    asked = []
    monkeypatch.setattr(check_updates, "year_exists", lambda year, c: asked.append(year) or False)

    result = check_updates.check(cfg)

    assert asked == [max(cfg["years"]) + 1]
    assert result == {"latest_configured": max(cfg["years"]), "candidate": max(cfg["years"]) + 1,
                      "new_data": False}


def test_the_message_says_what_to_do_when_there_is_new_data():
    text = check_updates.describe({"latest_configured": 2024, "candidate": 2025, "new_data": True})
    assert "NEW DATA" in text and "2025" in text and "config.toml" in text


def test_the_message_is_calm_when_there_is_nothing_new():
    text = check_updates.describe({"latest_configured": 2024, "candidate": 2025, "new_data": False})
    assert "No new annual file yet" in text


def test_main_reports_new_data_to_github_actions(cfg, monkeypatch, tmp_path, capsys):
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setattr(check_updates, "load_config", lambda: cfg)
    monkeypatch.setattr(check_updates, "year_exists", lambda year, c: True)

    assert check_updates.main() == 0

    lines = output_file.read_text().splitlines()
    assert lines == ["new_data=true", f"year={max(cfg['years']) + 1}"]
    assert "NEW DATA" in capsys.readouterr().out


def test_main_writes_nothing_when_not_running_in_github_actions(cfg, monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(check_updates, "load_config", lambda: cfg)
    monkeypatch.setattr(check_updates, "year_exists", lambda year, c: False)
    assert check_updates.main() == 0


def test_a_check_that_cannot_reach_bls_fails_loudly(cfg, monkeypatch, capsys):
    def no_network(*args, **kwargs):
        raise urllib.error.URLError("no connection")
    monkeypatch.setattr(check_updates, "load_config", lambda: cfg)
    monkeypatch.setattr(check_updates.urllib.request, "urlopen", no_network)

    assert check_updates.main() == 2
    assert "CHECK FAILED" in capsys.readouterr().err


def test_a_404_means_not_published_but_other_http_errors_are_failures(cfg, monkeypatch):
    def answer(code):
        def fake(*args, **kwargs):
            raise urllib.error.HTTPError("http://x", code, "msg", {}, None)
        return fake

    monkeypatch.setattr(check_updates.urllib.request, "urlopen", answer(404))
    assert check_updates.year_exists(2099, cfg) is False

    monkeypatch.setattr(check_updates.urllib.request, "urlopen", answer(503))
    with pytest.raises(check_updates.CheckError):
        check_updates.year_exists(2099, cfg)
