"""Live Google Sheet roster source (section 3) -- same CSV-export mechanism
as Desktop/ranking's extract.py."""
from __future__ import annotations

import pytest

from social_media_ranking.sheet_source import download_creator_sheet, raw_sheet_path
from social_media_ranking.storage import DataRepo


def test_download_writes_and_returns_the_cached_path(tmp_path):
    repo = DataRepo(tmp_path / "data")
    dest = download_creator_sheet("https://example.com/sheet.csv", fetch=lambda _: "name,url\nA,http://x\n", repo=repo)
    assert dest == raw_sheet_path(repo)
    assert dest.read_text(encoding="utf-8") == "name,url\nA,http://x\n"


def test_falls_back_to_the_cached_copy_on_a_failed_download(tmp_path):
    repo = DataRepo(tmp_path / "data")
    raw_sheet_path(repo).parent.mkdir(parents=True, exist_ok=True)
    raw_sheet_path(repo).write_text("name,url\nCached,http://x\n", encoding="utf-8")

    def failing_fetch(_url):
        raise RuntimeError("network down")

    dest = download_creator_sheet("https://example.com/sheet.csv", fetch=failing_fetch, repo=repo)
    assert dest.read_text(encoding="utf-8") == "name,url\nCached,http://x\n"


def test_raises_when_download_fails_and_nothing_is_cached(tmp_path):
    repo = DataRepo(tmp_path / "data")
    with pytest.raises(RuntimeError):
        download_creator_sheet("https://example.com/sheet.csv", fetch=lambda _: "", repo=repo)
