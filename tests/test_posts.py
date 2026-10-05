"""Section 5 — post cap/window, type classification, and the per-account cache."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from social_media_ranking.config import POST_CAP
from social_media_ranking.extractors.youtube import classify_video_type, parse_iso8601_duration
from social_media_ranking.models import Post
from social_media_ranking.posts_cache import cache_path, load_posts, merge_and_cap, save_posts
from social_media_ranking.storage import DataRepo


def _post(post_id: str, published_at: str, **kwargs) -> Post:
    return Post(platform="youtube", profile_url="https://youtube.com/@x", id=post_id, url="", published_at=published_at, **kwargs)


def test_parse_iso8601_duration_handles_hours_minutes_seconds():
    assert parse_iso8601_duration("PT4M13S") == 253
    assert parse_iso8601_duration("PT1H2M3S") == 3723
    assert parse_iso8601_duration("PT45S") == 45
    assert parse_iso8601_duration("") is None
    assert parse_iso8601_duration(None) is None


def test_classify_video_type_uses_the_sixty_second_boundary():
    assert classify_video_type(45) == "short"
    assert classify_video_type(60) == "short"
    assert classify_video_type(61) == "video"
    assert classify_video_type(None) == "other"


def test_merge_and_cap_dedupes_by_id_and_keeps_the_newest_first():
    now = datetime.now(timezone.utc)
    existing = [_post("1", (now - timedelta(days=1)).isoformat())]
    fetched = [
        _post("1", (now - timedelta(days=1)).isoformat(), title="updated"),  # same id, refreshed
        _post("2", now.isoformat()),
    ]
    merged = merge_and_cap(existing, fetched, now=now)
    assert [p.id for p in merged] == ["2", "1"]
    assert merged[1].title == "updated"


def test_merge_and_cap_drops_posts_outside_the_90_day_window():
    now = datetime.now(timezone.utc)
    old = _post("old", (now - timedelta(days=200)).isoformat())
    recent = _post("recent", (now - timedelta(days=1)).isoformat())
    merged = merge_and_cap([], [old, recent], now=now)
    assert [p.id for p in merged] == ["recent"]


def test_merge_and_cap_enforces_the_post_cap():
    now = datetime.now(timezone.utc)
    fetched = [_post(str(i), (now - timedelta(hours=i)).isoformat()) for i in range(POST_CAP + 10)]
    merged = merge_and_cap([], fetched, now=now)
    assert len(merged) == POST_CAP
    assert merged[0].id == "0"  # most recent kept


def test_posts_are_cached_per_account_and_reloadable(tmp_path):
    repo = DataRepo(tmp_path / "data")
    posts = [_post("1", "2026-09-01T00:00:00+00:00", title="hello")]
    path = save_posts(repo, "youtube", "https://youtube.com/@x", posts)
    assert path.is_file()
    assert path == cache_path(repo, "youtube", "https://youtube.com/@x")

    reloaded = load_posts(repo, "youtube", "https://youtube.com/@x")
    assert reloaded == posts


def test_load_posts_is_empty_when_nothing_cached_yet(tmp_path):
    repo = DataRepo(tmp_path / "data")
    assert load_posts(repo, "youtube", "https://youtube.com/@new") == []
