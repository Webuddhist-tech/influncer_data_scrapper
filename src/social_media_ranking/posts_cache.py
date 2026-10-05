"""Per-account post cache (section 5): fetch only what's new each week.

Posts live under ``data/cache/posts/{platform}/<hash of profile_url>.json`` —
a generated cache, never hand-edited, merged by id and re-capped to the
90-day / 100-post window on every write.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from .config import POST_CAP, POST_WINDOW_DAYS
from .models import Post
from .storage import DataRepo


def _safe_key(profile_url: str) -> str:
    return hashlib.sha1(profile_url.encode("utf-8")).hexdigest()[:16]


def cache_path(repo: DataRepo, platform: str, profile_url: str) -> Path:
    return repo.root / "cache" / "posts" / platform / f"{_safe_key(profile_url)}.json"


def parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def window_cutoff(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=POST_WINDOW_DAYS)


def load_posts(repo: DataRepo, platform: str, profile_url: str) -> List[Post]:
    path = cache_path(repo, platform, profile_url)
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8") or "[]")
    return [Post.from_row(row) for row in rows]


def newest_timestamp(posts: Iterable[Post]) -> Optional[datetime]:
    parsed = [parse_timestamp(p.published_at) for p in posts]
    parsed = [p for p in parsed if p is not None]
    return max(parsed) if parsed else None


def merge_and_cap(existing: Iterable[Post], fetched: Iterable[Post], now: Optional[datetime] = None) -> List[Post]:
    """Merge by id, drop anything outside the 90-day window, cap at 100, newest first."""
    cutoff = window_cutoff(now)
    by_id = {(p.platform, p.id): p for p in existing}
    for post in fetched:
        by_id[(post.platform, post.id)] = post

    def _within_window(post: Post) -> bool:
        ts = parse_timestamp(post.published_at)
        return ts is None or ts >= cutoff

    kept = [p for p in by_id.values() if _within_window(p)]
    kept.sort(key=lambda p: parse_timestamp(p.published_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return kept[:POST_CAP]


def save_posts(repo: DataRepo, platform: str, profile_url: str, posts: Iterable[Post]) -> Path:
    path = cache_path(repo, platform, profile_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([p.to_row() for p in posts], indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
