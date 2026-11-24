"""Tests for working out which years to pull. BLS is never contacted: year_exists is replaced."""

from datetime import date

import pytest

from qcew_pipeline import years
from qcew_pipeline.config import _check_years


def published_up_to(monkeypatch, newest):
    asked = []

    def fake(year, cfg):
        asked.append(year)
        return year <= newest
    monkeypatch.setattr(years, "year_exists", fake)
    return asked


def test_a_fixed_list_is_used_as_it_is_and_never_asks_bls(cfg, monkeypatch):
    asked = published_up_to(monkeypatch, 2099)
    cfg["years"] = [2024, 2022, 2023]
    assert years.resolve_years(cfg) == [2022, 2023, 2024]
    assert asked == []


def test_auto_gives_the_five_most_recent_years_ending_at_the_newest_published(cfg, monkeypatch):
    published_up_to(monkeypatch, 2025)
    cfg["years"], cfg["window_years"] = "auto", 5
    assert years.resolve_years(cfg, today=date(2026, 11, 23)) == [2021, 2022, 2023, 2024, 2025]


def test_the_window_length_is_one_setting(cfg, monkeypatch):
    published_up_to(monkeypatch, 2025)
    cfg["years"], cfg["window_years"] = "auto", 6
    assert years.resolve_years(cfg, today=date(2026, 11, 23)) == [2020, 2021, 2022, 2023, 2024, 2025]


def test_the_window_moves_forward_when_bls_publishes_a_new_year(cfg, monkeypatch):
    cfg["years"], cfg["window_years"] = "auto", 5
    published_up_to(monkeypatch, 2025)
    before = years.resolve_years(cfg, today=date(2026, 11, 23))
    published_up_to(monkeypatch, 2026)
    after = years.resolve_years(cfg, today=date(2027, 10, 1))
    assert (before[-1], after[-1]) == (2025, 2026)
    assert after == [2022, 2023, 2024, 2025, 2026]


def test_it_looks_from_this_year_backwards_and_stops_at_the_first_hit(cfg, monkeypatch):
    asked = published_up_to(monkeypatch, 2025)
    assert years.latest_published_year(cfg, today=date(2026, 11, 23)) == 2025
    assert asked == [2026, 2025]


def test_it_fails_loudly_when_no_recent_file_exists(cfg, monkeypatch):
    published_up_to(monkeypatch, 1999)
    with pytest.raises(years.CheckError):
        years.latest_published_year(cfg, today=date(2026, 11, 23))


@pytest.mark.parametrize("bad", [[], "latest", [2023, "2024"], 2024, None])
def test_a_years_setting_that_cannot_be_used_is_refused_with_a_plain_message(bad):
    with pytest.raises(ValueError, match="years must be"):
        _check_years({"years": bad})


@pytest.mark.parametrize("bad", [0, -1, "five", True, None])
def test_auto_needs_a_sensible_window(bad):
    with pytest.raises(ValueError, match="window_years"):
        _check_years({"years": "auto", "window_years": bad})
