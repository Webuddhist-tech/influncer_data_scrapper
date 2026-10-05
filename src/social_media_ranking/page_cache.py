"""On-disk cache for fetched Linktree pages (`data/cache/pages/`).

Same idea as Desktop/ranking's `extract.py`: downloaded pages are cached for
a TTL (14 days, matching that project), so re-running `extract-links` against
the *entire* sheet every week is cheap — most pages are a cache hit, and only
new or stale ones actually hit the network. That's what lets a full extract
run weekly instead of only monthly: a brand-new row in the sheet is picked up
on the very next run, not next month's.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable

from .storage import DataRepo

CACHE_TTL_SECONDS = 14 * 24 * 60 * 60


def _cache_path(repo: DataRepo, url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return repo.root / "cache" / "pages" / f"{digest}.html"


def _is_fresh(path: Path) -> bool:
    if not path.is_file():
        return False
    # A tiny file is more likely an error page than a real Linktree profile.
    if path.stat().st_size <= 1000:
        return False
    return (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS


class CachedFetcher:
    """Wraps a ``fetch(url) -> str`` callable with the on-disk page cache."""

    def __init__(self, repo: DataRepo, fetch: Callable[[str], str], refresh: bool = False):
        self.repo = repo
        self.fetch = fetch
        self.refresh = refresh

    def __call__(self, url: str) -> str:
        path = _cache_path(self.repo, url)
        if not self.refresh and _is_fresh(path):
            return path.read_text(encoding="utf-8", errors="replace")

        html = self.fetch(url)
        if html:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
        return html
