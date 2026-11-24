"""Tests for reading config.toml and the environment-variable overrides."""

from pathlib import Path

import pytest

from qcew_pipeline.config import PROJECT_ROOT, load_config


def test_the_default_settings_load_and_paths_become_absolute(monkeypatch):
    for name in ("QCEW_RAW_DIR", "QCEW_DB_PATH", "QCEW_OUTPUT_DIR", "QCEW_USER_AGENT"):
        monkeypatch.delenv(name, raising=False)
    cfg = load_config()
    assert cfg["years"] == "auto" and cfg["window_years"] == 5
    assert cfg["paths"]["db_path"] == PROJECT_ROOT / "data" / "qcew_dmv.sqlite"
    assert all(Path(p).is_absolute() for p in cfg["paths"].values())


def test_environment_variables_win_over_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv("QCEW_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("QCEW_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("QCEW_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("QCEW_USER_AGENT", "someone-else/1.0 (me@example.org)")

    cfg = load_config()

    assert cfg["paths"]["raw_dir"] == tmp_path / "raw"
    assert cfg["paths"]["db_path"] == tmp_path / "db.sqlite"
    assert cfg["paths"]["output_dir"] == tmp_path / "out"
    assert cfg["download"]["user_agent"] == "someone-else/1.0 (me@example.org)"


def test_a_settings_file_with_unusable_years_is_refused(tmp_path):
    text = (PROJECT_ROOT / "config.toml").read_text(encoding="utf-8")
    assert text.count('years = "auto"') == 1
    bad = tmp_path / "bad.toml"
    bad.write_text(text.replace('years = "auto"', "years = []"), encoding="utf-8")
    with pytest.raises(ValueError, match="years must be"):
        load_config(bad)
