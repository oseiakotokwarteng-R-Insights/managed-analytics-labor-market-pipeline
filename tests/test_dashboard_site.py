"""Tests for the dashboard's pages themselves (not the data): the footer, the files they use, and no outside requests.

These can't draw the charts. They guard the things that quietly break: a missing file, a typo in an import,
a script loaded from somewhere else, or the Managed Analytics footer going missing.
"""

import re
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parent.parent / "dashboard"
INDEX = (SITE / "index.html").read_text(encoding="utf-8")


def local_files():
    return sorted(p for p in SITE.rglob("*") if p.is_file() and "data" not in p.relative_to(SITE).parts[:1])


def test_the_footer_says_who_it_is_powered_by_and_links_to_the_site():
    footer = re.search(r"<footer.*?</footer>", INDEX, re.S).group(0)
    assert "Powered by" in footer
    assert re.search(r'<a href="https://managedanalytics\.co"[^>]*>Managed Analytics</a>', footer)
    assert 'rel="noopener"' in footer  # it opens in a new tab, so the dashboard stays where it is


def test_every_file_the_page_points_to_exists():
    for ref in re.findall(r'(?:src|href)="([^"#]+)"', INDEX):
        if ref.startswith(("http", "data:", "mailto:")):
            continue
        assert (SITE / ref).is_file(), f"index.html points to {ref}, which isn't there"


@pytest.mark.parametrize("script", [p for p in SITE.rglob("*.js")], ids=lambda p: p.relative_to(SITE).as_posix())
def test_every_script_import_resolves(script):
    source = script.read_text(encoding="utf-8")
    for target in re.findall(r"""from\s+['"](\.{1,2}/[^'"]+)['"]""", source):
        assert (script.parent / target).resolve().is_file(), f"{script.name} imports {target}, which isn't there"


def test_nothing_is_loaded_from_another_website_except_links_people_click():
    """No outside scripts, styles, fonts or trackers. The pages work offline once served."""
    for p in local_files():
        if p.suffix in {".html", ".css", ".js"}:
            text = p.read_text(encoding="utf-8")
            assert not re.search(r"<script[^>]+src=[\"']https?:", text), p.name
            assert not re.search(r"<link[^>]+href=[\"']https?:", text), p.name
            assert "@import" not in text and "url(http" not in text, p.name
            assert "fetch(`http" not in text and "fetch('http" not in text, p.name


def test_the_data_is_read_from_the_data_folder_only():
    fetches = re.findall(r"fetch\(([^)]*)\)", (SITE / "js" / "lib.js").read_text(encoding="utf-8"))
    assert fetches and all("data/" in f for f in fetches)


def test_every_menu_link_has_a_view_and_every_view_has_a_menu_link():
    menu = set(re.findall(r'data-route="(\w+)"', INDEX))
    app = (SITE / "js" / "app.js").read_text(encoding="utf-8")
    views = set(re.search(r"const views = \{([^}]*)\}", app).group(1).replace(" ", "").split(","))
    assert menu == views
    for name in views:
        assert (SITE / "js" / "views" / f"{name}.js").is_file()


def test_the_page_has_what_a_screen_reader_user_needs():
    assert '<html lang="en">' in INDEX and 'class="skip"' in INDEX and '<main id="main"' in INDEX
    assert 'aria-label="Sections"' in INDEX and "<title>" in INDEX


def test_generated_data_is_kept_out_of_version_control():
    ignore = (SITE.parent / ".gitignore").read_text(encoding="utf-8")
    assert "dashboard/data/" in ignore
