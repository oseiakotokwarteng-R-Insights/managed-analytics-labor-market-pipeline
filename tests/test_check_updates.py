"""Tests for the new-data check. BLS is never contacted: the web request is replaced."""

import urllib.error

import pytest

from qcew_pipeline import check_updates, years


def newest(monkeypatch, year):
    monkeypatch.setattr(years, "latest_published_year", lambda cfg, today=None: year)


def test_in_auto_mode_it_reports_the_newest_year_and_the_window(cfg, monkeypatch):
    newest(monkeypatch, 2025)
    cfg["years"], cfg["window_years"] = "auto", 5

    result = check_updates.check(cfg)

    assert result == {
        "mode": "auto",
        "latest_published": 2025,
        "window": [2021, 2022, 2023, 2024, 2025],
        "new_data": True,
    }
    assert "2021-2025" in check_updates.describe(result)


def test_with_a_fixed_list_news_means_a_year_after_the_last_one(cfg, monkeypatch):
    cfg["years"] = [2022, 2023, 2024]
    newest(monkeypatch, 2025)
    assert check_updates.check(cfg)["new_data"] is True
    newest(monkeypatch, 2024)
    assert check_updates.check(cfg)["new_data"] is False


def test_the_message_says_what_to_do_when_a_fixed_list_is_behind(cfg, monkeypatch):
    cfg["years"] = [2022, 2023, 2024]
    newest(monkeypatch, 2025)
    text = check_updates.describe(check_updates.check(cfg))
    assert "NEW DATA" in text and "2025" in text and "config.toml" in text


def test_the_message_is_calm_when_a_fixed_list_is_up_to_date(cfg, monkeypatch):
    cfg["years"] = [2022, 2023, 2024]
    newest(monkeypatch, 2024)
    assert "No new annual file yet" in check_updates.describe(check_updates.check(cfg))


def test_main_reports_to_github_actions(cfg, monkeypatch, tmp_path, capsys):
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    cfg["years"], cfg["window_years"] = "auto", 5
    monkeypatch.setattr(check_updates, "load_config", lambda: cfg)
    newest(monkeypatch, 2025)

    assert check_updates.main() == 0

    assert output_file.read_text().splitlines() == ["new_data=true", "year=2025", "window=2021-2025"]
    assert "2021-2025" in capsys.readouterr().out


def test_main_writes_nothing_when_not_running_in_github_actions(cfg, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(check_updates, "load_config", lambda: cfg)
    newest(monkeypatch, 2024)
    assert check_updates.main() == 0


def test_a_check_that_cannot_reach_bls_fails_loudly(cfg, monkeypatch, capsys):
    def no_network(*args, **kwargs):
        raise urllib.error.URLError("no connection")

    monkeypatch.setattr(check_updates, "load_config", lambda: cfg)
    monkeypatch.setattr(years.urllib.request, "urlopen", no_network)

    assert check_updates.main() == 2
    assert "CHECK FAILED" in capsys.readouterr().err


def test_a_404_means_not_published_but_other_http_errors_are_failures(cfg, monkeypatch):
    def answer(code):
        def fake(*args, **kwargs):
            raise urllib.error.HTTPError("http://x", code, "msg", {}, None)

        return fake

    monkeypatch.setattr(years.urllib.request, "urlopen", answer(404))
    assert years.year_exists(2099, cfg) is False

    monkeypatch.setattr(years.urllib.request, "urlopen", answer(503))
    with pytest.raises(years.CheckError):
        years.year_exists(2099, cfg)
