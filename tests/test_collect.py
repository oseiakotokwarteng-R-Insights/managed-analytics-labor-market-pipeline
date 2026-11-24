"""Tests for downloading from BLS. Nothing touches the network: the web request is replaced."""

import hashlib
import urllib.error

import pytest

from qcew_pipeline import collect


class FakeResponse:
    """Stands in for the object urllib returns: readable in pieces, and usable in a `with` block.

    With fail_after set, it hands out 4 bytes at a time and the connection drops once that
    many bytes have been sent.
    """

    def __init__(self, data=b"", fail_after=None):
        self.data, self.fail_after, self.sent = data, fail_after, 0

    def read(self, size=-1):
        if size == -1:
            return self.data
        if self.fail_after is not None and self.sent >= self.fail_after:
            raise ConnectionError("connection dropped")
        step = 4 if self.fail_after is not None else size
        chunk = self.data[self.sent : self.sent + step]
        self.sent += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(collect.time, "sleep", lambda seconds: None)


def serve(monkeypatch, *responses):
    """Make each call to BLS return (or raise) the next item. Returns the list of calls made."""
    calls, queue = [], list(responses)

    def fake_open(url, cfg):
        calls.append(url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(collect, "_open", fake_open)
    return calls


def test_the_fingerprint_matches_a_known_sha256(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert collect.sha256_of_file(f) == hashlib.sha256(b"hello").hexdigest()


def test_a_download_is_saved_fingerprinted_and_described(cfg, monkeypatch):
    calls = serve(monkeypatch, FakeResponse(b"zipdata" * 100))

    info = collect.download_annual_file(2024, cfg)

    assert info["download_status"] == "downloaded" and info["year"] == 2024
    assert info["url"].endswith("2024_annual_singlefile.zip") and calls == [info["url"]]
    assert info["size_bytes"] == 700
    assert info["sha256"] == hashlib.sha256(b"zipdata" * 100).hexdigest()
    assert not list(cfg["paths"]["raw_dir"].glob("*.part"))  # the temporary file was renamed


def test_a_file_already_on_disk_is_reused_not_downloaded_again(cfg, monkeypatch):
    cfg["paths"]["raw_dir"].mkdir(parents=True)
    (cfg["paths"]["raw_dir"] / "2024_annual_singlefile.zip").write_bytes(b"already here")
    calls = serve(monkeypatch)  # any download would fail the test

    info = collect.download_annual_file(2024, cfg)

    assert info["download_status"] == "reused_existing" and calls == []
    assert info["sha256"] == hashlib.sha256(b"already here").hexdigest()


def test_reuse_can_be_switched_off(cfg, monkeypatch):
    cfg["download"]["reuse_existing_files"] = False
    cfg["paths"]["raw_dir"].mkdir(parents=True)
    (cfg["paths"]["raw_dir"] / "2024_annual_singlefile.zip").write_bytes(b"old")
    serve(monkeypatch, FakeResponse(b"new"))

    info = collect.download_annual_file(2024, cfg)

    assert info["download_status"] == "downloaded" and info["size_bytes"] == 3


def test_a_failed_attempt_is_retried(cfg, monkeypatch):
    calls = serve(monkeypatch, urllib.error.URLError("busy"), TimeoutError("slow"), FakeResponse(b"ok"))

    info = collect.download_annual_file(2024, cfg)

    assert info["download_status"] == "downloaded" and len(calls) == 3


def test_it_gives_up_after_the_configured_number_of_tries(cfg, monkeypatch):
    cfg["download"]["retries"] = 2
    calls = serve(monkeypatch, urllib.error.URLError("down"), urllib.error.URLError("down"))

    with pytest.raises(collect.DownloadError):
        collect.download_annual_file(2024, cfg)

    assert len(calls) == 2


def test_a_404_is_not_retried(cfg, monkeypatch):
    calls = serve(monkeypatch, urllib.error.HTTPError("http://x", 404, "Not Found", {}, None))

    with pytest.raises(collect.DownloadError):
        collect.download_annual_file(2099, cfg)

    assert len(calls) == 1


def test_a_download_that_breaks_halfway_leaves_no_file_that_looks_finished(cfg, monkeypatch):
    cfg["download"]["retries"] = 1
    serve(monkeypatch, FakeResponse(b"x" * 100, fail_after=8))

    with pytest.raises(collect.DownloadError):
        collect.download_annual_file(2024, cfg)

    assert not (cfg["paths"]["raw_dir"] / "2024_annual_singlefile.zip").exists()


# ---------------------------------------------------------------- the small code-title files
def test_reference_tables_keep_codes_as_text_including_leading_zeros(cfg, monkeypatch):
    text = "﻿code,title\n01,Agriculture\n31-33,Manufacturing\n"
    serve(monkeypatch, FakeResponse(text.encode("utf-8")))

    df = collect.fetch_reference_table("industry_titles", cfg)

    assert list(df.columns) == ["code", "title"]  # the byte-order mark was removed
    assert df["code"].tolist() == ["01", "31-33"]


def test_a_reference_table_that_cannot_be_downloaded_raises_a_clear_error(cfg, monkeypatch):
    serve(monkeypatch, urllib.error.URLError("down"))
    with pytest.raises(collect.DownloadError):
        collect.fetch_reference_table("area_titles", cfg)


def test_all_four_reference_tables_are_fetched(cfg, monkeypatch):
    serve(monkeypatch, *[FakeResponse(b"code,title\n1,a\n") for _ in range(4)])
    tables = collect.fetch_reference_tables(cfg)
    assert sorted(tables) == ["agglevel", "area", "industry", "ownership"]
