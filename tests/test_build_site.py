"""Tests for the script that puts the dashboard website together."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "build_site.py"
spec = importlib.util.spec_from_file_location("build_site", SCRIPT)
build_site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_site)


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "some_data"
    (folder / "sectors").mkdir(parents=True)
    for name in build_site.REQUIRED_DATA:
        (folder / name).write_text(json.dumps({"name": name}), encoding="utf-8")
    (folder / "sectors" / "24031.json").write_text("{}", encoding="utf-8")
    return folder


def test_the_site_is_the_pages_plus_the_data(data, tmp_path):
    out = build_site.build(tmp_path / "site", data)
    assert (out / "index.html").is_file() and (out / "js" / "app.js").is_file() and (out / "css" / "style.css").is_file()
    assert (out / "data" / "meta.json").is_file() and (out / "data" / "sectors" / "24031.json").is_file()


def test_the_footer_is_in_the_built_page(data, tmp_path):
    page = (build_site.build(tmp_path / "site", data) / "index.html").read_text(encoding="utf-8")
    assert "Powered by" in page and "https://managedanalytics.co" in page


def test_leftover_generated_data_in_the_dashboard_folder_is_not_copied(data, tmp_path, monkeypatch):
    stale = build_site.DASHBOARD / "data"
    existed = stale.exists()
    stale.mkdir(exist_ok=True)
    marker = stale / "stale_marker.json"
    marker.write_text("{}", encoding="utf-8")
    try:
        out = build_site.build(tmp_path / "site", data)
        assert not (out / "data" / "stale_marker.json").exists()
    finally:
        marker.unlink()
        if not existed:
            stale.rmdir()


def test_the_site_says_it_only_loads_its_own_files(data, tmp_path):
    headers = (build_site.build(tmp_path / "site", data) / "_headers").read_text(encoding="utf-8")
    assert "default-src 'self'" in headers and "script-src 'self'" in headers
    assert "unsafe-eval" not in headers and "script-src 'self' 'unsafe-inline'" not in headers  # scripts are never inline
    assert "/data/*" in headers and "no-cache" in headers


def test_missing_data_is_refused_with_a_clear_message(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit, match="Dashboard data workflow"):
        build_site.build(tmp_path / "site", empty)
    assert not (tmp_path / "site").exists()  # no half-built site left behind


def test_building_twice_starts_clean(data, tmp_path):
    out = build_site.build(tmp_path / "site", data)
    (out / "leftover.txt").write_text("x", encoding="utf-8")
    build_site.build(tmp_path / "site", data)
    assert not (out / "leftover.txt").exists()
