"""On-disk Linktree page cache: cache hits, staleness, and --refresh."""
from __future__ import annotations

import os
import time

from social_media_ranking.page_cache import CACHE_TTL_SECONDS, CachedFetcher, _cache_path
from social_media_ranking.storage import DataRepo


def test_second_call_is_served_from_cache_not_the_network(tmp_path):
    repo = DataRepo(tmp_path / "data")
    calls = []

    def fetch(url):
        calls.append(url)
        return "<html>" + "x" * 2000 + "</html>"

    cached = CachedFetcher(repo, fetch)
    first = cached("https://linktr.ee/natgeo")
    second = cached("https://linktr.ee/natgeo")

    assert first == second
    assert calls == ["https://linktr.ee/natgeo"]  # only fetched once


def test_refresh_bypasses_the_cache(tmp_path):
    repo = DataRepo(tmp_path / "data")
    calls = []

    def fetch(url):
        calls.append(url)
        return "<html>" + "x" * 2000 + "</html>"

    CachedFetcher(repo, fetch)("https://linktr.ee/natgeo")
    CachedFetcher(repo, fetch, refresh=True)("https://linktr.ee/natgeo")
    assert len(calls) == 2


def test_a_stale_cache_entry_is_refetched(tmp_path):
    repo = DataRepo(tmp_path / "data")
    calls = []

    def fetch(url):
        calls.append(url)
        return "<html>" + "x" * 2000 + "</html>"

    cached = CachedFetcher(repo, fetch)
    cached("https://linktr.ee/natgeo")

    path = _cache_path(repo, "https://linktr.ee/natgeo")
    old_time = time.time() - CACHE_TTL_SECONDS - 60
    os.utime(path, (old_time, old_time))

    cached("https://linktr.ee/natgeo")
    assert len(calls) == 2


def test_a_tiny_cached_page_is_treated_as_an_error_page_not_a_real_profile(tmp_path):
    repo = DataRepo(tmp_path / "data")
    calls = []

    def fetch(url):
        calls.append(url)
        return "oops"  # under the 1000-byte floor

    cached = CachedFetcher(repo, fetch)
    cached("https://linktr.ee/dead")
    cached("https://linktr.ee/dead")
    assert len(calls) == 2  # never trusted as a cache hit
