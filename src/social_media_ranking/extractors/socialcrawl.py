"""SocialCrawl extractor (section 4), against the real API.

See docs/socialcrawl-fields.md for how these endpoints and field names were
chosen. Profile lookups for handle-keyed platforms (Instagram, TikTok,
Threads, X) go through the real bulk endpoint, ``POST /v1/prism/profiles``
(up to 50 accounts per call) — an earlier version of this file called the
single-account `GET .../profile` endpoint once per account, missing this
entirely. Facebook and LinkedIn stay on the single-account path: Prism's
bulk ``items`` are ``(platform, handle)`` pairs and both of those are
URL-keyed here, so whether a page name / vanity slug works the same way in
bulk is unconfirmed without a live key. Posts still use the per-account
endpoints regardless of platform — Prism's bulk ``include: "posts"`` only
returns each handle's first page, not the full 90-day/100-post window this
pipeline needs. Concurrency (for posts, and for the url-keyed platforms'
profile calls) is bounded by a thread pool and a shared rate limiter,
matching the documented 600/minute and 50-concurrent-request ceiling.
Batching cuts call counts, not credits — Prism bills the same per resolved
handle as calling it individually.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence

import requests

from ..config import (
    Config,
    POST_CAP,
    SOCIALCRAWL_MAX_CONCURRENCY_CEILING,
    SOCIALCRAWL_MAX_RETRIES,
    SOCIALCRAWL_RATE_LIMIT_PER_MINUTE,
)
from ..http import HttpClient
from ..models import (
    RESOLUTION_ERROR,
    RESOLUTION_NOT_FOUND,
    RESOLUTION_OK,
    RESOLUTION_PRIVATE,
    CreatorProfile,
    Post,
    RawMetrics,
)
from ..posts_cache import load_posts, merge_and_cap, save_posts
from ..storage import DataRepo, utc_now

log = logging.getLogger(__name__)


def chunk(items: Sequence[Any], size: int) -> List[List[Any]]:
    """Split a sequence into batches of at most ``size``.

    Not used for SocialCrawl calls (there's no batch endpoint) — kept for
    YouTube's genuine 50-ID-per-call ``channels.list``/``videos.list``.
    """
    if size < 1:
        raise ValueError("batch size must be at least 1")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


# Real endpoints, confirmed against SocialCrawl's public docs (not a live
# key — see docs/socialcrawl-fields.md for what still needs live
# confirmation). ``api_platform`` is SocialCrawl's own path segment, which
# isn't always our platform key (X is still "/twitter/..." there).
PLATFORM_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "instagram": {
        "api_platform": "instagram",
        "param": "handle",
        "profile": "/instagram/profile",
        "posts": [
            {"path": "/instagram/profile/posts", "cursor_param": "next_max_id", "default_type": "image", "extra": {}},
            # Reels are where Instagram exposes view counts (brief section 4).
            {"path": "/instagram/profile/reels", "cursor_param": "max_id", "default_type": "short", "extra": {}},
        ],
    },
    "facebook": {
        "api_platform": "facebook",
        "param": "url",
        "profile": "/facebook/profile",
        # Confirmed live 2026-10-05: with no limit param at all, every
        # account's posts call hit a 402 Payment Required on a low-credit
        # account (the profile call itself was unaffected). Threads showed
        # the same pattern and a small `limit` fixed it there, so applying
        # the same defensively here -- not re-verified live since this was
        # already the last credits available this run.
        "posts": [{"path": "/facebook/profile/posts", "cursor_param": "cursor", "default_type": "other", "extra": {"limit": 10}}],
    },
    "tiktok": {
        "api_platform": "tiktok",
        "param": "handle",
        "profile": "/tiktok/profile",
        "posts": [{"path": "/tiktok/profile/videos", "cursor_param": "max_cursor", "default_type": "short", "extra": {}}],
    },
    "threads": {
        "api_platform": "threads",
        "param": "handle",
        "profile": "/threads/profile",
        # No documented cursor param for this endpoint -- one page, capped by
        # limit. Confirmed live 2026-10-05: limit=50 triggered a 402 Payment
        # Required on a low-credit account -- its cost scales with limit,
        # not a flat rate (docs/socialcrawl-fields.md), and 50 was too much.
        # Dropped to 10 as a conservative default; exact cost-per-limit
        # ratio still unconfirmed.
        "posts": [
            {"path": "/threads/user/posts", "cursor_param": None, "default_type": "text", "extra": {"limit": 10, "include": "engagement"}}
        ],
    },
    "x": {
        "api_platform": "twitter",
        "param": "handle",
        "profile": "/twitter/profile",
        "posts": [{"path": "/twitter/user/tweets", "cursor_param": "cursor", "default_type": "text", "extra": {}}],
    },
    "linkedin": {
        "api_platform": "linkedin",
        "param": "url",
        "profile": "/linkedin/profile",
        "posts": [{"path": "/linkedin/profile/posts", "cursor_param": None, "default_type": "text", "extra": {"limit": 50}}],
    },
}


# Prism bulk profiles (docs/socialcrawl-fields.md): up to 50 (platform,
# handle) pairs per POST /v1/prism/profiles call. Scoped to handle-keyed
# platforms only -- see the module docstring for why Facebook/LinkedIn are
# excluded.
PRISM_BATCH_SIZE = 50
PRISM_BULK_PLATFORMS = {"instagram", "tiktok", "threads", "x"}
# Prism's bulk endpoint names this platform "x"; the single-account profile
# endpoint is still "/twitter/profile" (SocialCrawl's own legacy path).
PRISM_PLATFORM_NAMES: Dict[str, str] = {"x": "x"}


def estimate_calls(profiles: Sequence[CreatorProfile], platform: str, use_bulk_profiles: bool = False) -> int:
    """Dry-run estimate: profile call(s) plus one call per posts endpoint
    per account (a floor — real pagination may cost more)."""
    spec = PLATFORM_ENDPOINTS[platform]
    n = len(list(profiles))
    if use_bulk_profiles and platform in PRISM_BULK_PLATFORMS:
        profile_calls = -(-n // PRISM_BATCH_SIZE)  # ceil(n / 50)
    else:
        profile_calls = n
    return profile_calls + n * len(spec["posts"])


# Confirmed per-call credit costs (docs/socialcrawl-fields.md). Assumes one
# page per posts endpoint reaches the 100-post cap -- a floor, not a
# ceiling: several endpoints don't document their page size, so an account
# with more than one page of recent posts costs more than this. Threads'
# posts call in particular scales with the `limit` param, not a flat rate.
CREDIT_COSTS: Dict[str, Dict[str, Any]] = {
    "instagram": {"profile": 1, "posts": [1, 1]},  # /posts, /reels
    "facebook": {"profile": 1, "posts": [1]},
    "tiktok": {"profile": 1, "posts": [1]},
    "threads": {"profile": 1, "posts": [1]},
    "x": {"profile": 1, "posts": [1]},
    "linkedin": {"profile": 5, "posts": [5]},
}


def estimate_credits(profiles: Sequence[CreatorProfile], platform: str) -> int:
    """Dry-run credit estimate (not just a call count) -- see CREDIT_COSTS."""
    costs = CREDIT_COSTS[platform]
    per_account = costs["profile"] + sum(costs["posts"])
    return len(list(profiles)) * per_account


def _first_number(entry: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = entry.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _as_int(value: Optional[float]) -> Optional[int]:
    return None if value is None else int(value)


def _account_value(profile: CreatorProfile, spec: Dict[str, Any]) -> str:
    return profile.profile_url if spec["param"] == "url" else profile.handle.lstrip("@")


# Twitter/X uses "_count" suffixes; everyone else uses the bare noun.
FOLLOWERS_ALIASES = {"x": ("followers_count", "followers", "follower_count")}
DEFAULT_FOLLOWERS_ALIASES = ("followers", "follower_count")


def parse_profile(profile: CreatorProfile, author: Dict[str, Any]) -> RawMetrics:
    followers = _as_int(_first_number(author, *FOLLOWERS_ALIASES.get(profile.platform, DEFAULT_FOLLOWERS_ALIASES)))
    return RawMetrics(
        creator_name=profile.creator_name,
        platform=profile.platform,
        handle=profile.handle,
        profile_url=profile.profile_url,
        followers=followers,
        verified=bool(author.get("verified")) if "verified" in author else None,
        account_created_at=str(author.get("created_at") or author.get("joined_at") or ""),
        last_updated_at=utc_now(),
        resolution_status=RESOLUTION_OK,
        followers_source="socialcrawl",
        raw_snippet=str(author)[:500],
    )


def _error_metrics(profile: CreatorProfile, status: str, detail: str) -> RawMetrics:
    return RawMetrics(
        creator_name=profile.creator_name,
        platform=profile.platform,
        handle=profile.handle,
        profile_url=profile.profile_url,
        last_updated_at=utc_now(),
        resolution_status=status,
        raw_snippet=detail[:500],
    )


def parse_post(platform: str, profile: CreatorProfile, item: Dict[str, Any], default_type: str) -> Post:
    """One post, platform-agnostic: SocialCrawl's docs describe a shared
    Author/Post/engagement shape even where exact field names still need
    live confirmation (docs/socialcrawl-fields.md)."""
    engagement = item.get("engagement") or item.get("engagement_metrics") or item
    content = item.get("content") or {}
    caption = str(
        item.get("caption") or item.get("text") or content.get("text") or item.get("message") or ""
    )
    media_urls = item.get("media_urls") or content.get("media_urls") or []
    thumbnail = item.get("thumbnail_url") or content.get("thumbnail_url") or (media_urls[0] if media_urls else None)
    published = str(
        item.get("published_at") or item.get("timestamp") or item.get("published_time") or ""
    )
    duration = _as_int(_first_number(item, "duration", "duration_s", "video_duration"))
    views = _as_int(_first_number(engagement, "views", "view_count", "play_count"))
    likes = _as_int(_first_number(engagement, "likes", "like_count"))
    comments = _as_int(_first_number(engagement, "comments", "comment_count", "replies", "reply_count"))
    shares = _as_int(
        _first_number(engagement, "shares", "share_count", "retweets", "retweet_count", "reshare_count")
    )

    media_type = str(item.get("type") or item.get("media_type") or "").lower()
    if media_type in {"video", "reel", "short"}:
        post_type = "short" if duration is not None and duration <= 60 else "video"
    elif media_type in {"carousel", "carousel_album"}:
        post_type = "carousel"
    elif media_type in {"image", "photo"}:
        post_type = "image"
    elif media_type in {"text", "status"}:
        post_type = "text"
    elif media_type:
        post_type = "other"
    else:
        post_type = default_type

    return Post(
        platform=platform,
        profile_url=profile.profile_url,
        id=str(item.get("id") or ""),
        url=str(item.get("url") or item.get("permalink") or ""),
        published_at=published,
        type=post_type,
        title=caption[:140],
        thumbnail_url=thumbnail,
        duration_s=duration,
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
    )


class _RateLimiter:
    """Shared across every SocialCrawl call this run: stays under the
    documented 600/minute per key, regardless of thread count."""

    def __init__(self, per_minute: int = SOCIALCRAWL_RATE_LIMIT_PER_MINUTE):
        self._per_minute = per_minute
        self._lock = threading.Lock()
        self._timestamps: "deque[float]" = deque()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] > 60:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._per_minute:
                time.sleep(max(0.0, 60 - (now - self._timestamps[0])))
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] > 60:
                    self._timestamps.popleft()
            self._timestamps.append(time.monotonic())


class SocialCrawlExtractor:
    def __init__(
        self,
        config: Config,
        client: Optional[HttpClient] = None,
        rate_limiter: Optional[_RateLimiter] = None,
    ):
        self.config = config
        # Section 4: max 4 tries on 429/5xx, independent of the pipeline's
        # generic HTTP retry count.
        self.client = client or HttpClient(config.request_timeout, SOCIALCRAWL_MAX_RETRIES)
        self.rate_limiter = rate_limiter or _RateLimiter()
        self.credits_used = 0
        self._credits_lock = threading.Lock()

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self.config.socialcrawl_api_key}

    def _call(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.rate_limiter.acquire()
        url = f"{self.config.socialcrawl_base_url}{path}"
        response = self.client.get_json(url, params=params, headers=self._headers())
        with self._credits_lock:
            self.credits_used += int(response.get("credits_used") or 0)
        return response

    def _call_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.rate_limiter.acquire()
        url = f"{self.config.socialcrawl_base_url}{path}"
        response = self.client.post_json(url, payload, headers=self._headers())
        with self._credits_lock:
            self.credits_used += int(response.get("credits_used") or 0)
        return response

    def fetch_profiles_bulk(self, profiles: Sequence[CreatorProfile], platform: str) -> Dict[str, RawMetrics]:
        """One POST per 50 accounts via Prism instead of one GET per account.

        Confirmed live 2026-10-05 against a real key: the envelope is
        ``data.results[]``, each row shaped
        ``{index, platform, target: {platform, handle}, status, data:
        {author: {...}, computed: {...}}, cost}``. Matched by the row's own
        ``index`` back to the request batch, not by zip() order, in case a
        future response is ever reordered.
        """
        results: Dict[str, RawMetrics] = {}
        prism_platform = PRISM_PLATFORM_NAMES.get(platform, platform)
        for batch in chunk(list(profiles), PRISM_BATCH_SIZE):
            items = [{"platform": prism_platform, "handle": p.handle.lstrip("@")} for p in batch]
            try:
                response = self._call_post("/prism/profiles", {"items": items})
            except Exception as exc:  # noqa: BLE001 - one bad batch must not abort the whole platform
                for profile in batch:
                    results[profile.profile_url] = _error_metrics(profile, RESOLUTION_ERROR, str(exc))
                continue

            rows = (response.get("data") or {}).get("results") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                index = row.get("index")
                if not isinstance(index, int) or not (0 <= index < len(batch)):
                    continue
                profile = batch[index]

                status = str(row.get("status", "ok")).lower()
                if status == "not_found":
                    results[profile.profile_url] = _error_metrics(profile, RESOLUTION_NOT_FOUND, str(row.get("error") or row)[:500])
                    continue
                if status != "ok":
                    results[profile.profile_url] = _error_metrics(profile, RESOLUTION_ERROR, str(row.get("error") or row)[:500])
                    continue

                author = (row.get("data") or {}).get("author") or {}
                if author.get("private"):
                    results[profile.profile_url] = _error_metrics(profile, RESOLUTION_PRIVATE, "private account")
                    continue
                results[profile.profile_url] = parse_profile(profile, author)

            for profile in batch:  # a row missing entirely -- don't silently drop anyone
                if profile.profile_url not in results:
                    results[profile.profile_url] = _error_metrics(profile, RESOLUTION_ERROR, "no row returned for this account")
        return results

    def fetch_profile(self, profile: CreatorProfile, spec: Dict[str, Any]) -> RawMetrics:
        try:
            response = self._call(spec["profile"], {spec["param"]: _account_value(profile, spec)})
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                return _error_metrics(profile, RESOLUTION_NOT_FOUND, str(exc))
            return _error_metrics(profile, RESOLUTION_ERROR, str(exc))
        except Exception as exc:  # noqa: BLE001 - one bad account must not abort the run
            return _error_metrics(profile, RESOLUTION_ERROR, str(exc))

        data = response.get("data") or {}
        author = data.get("author", data)
        if author.get("private"):
            return _error_metrics(profile, RESOLUTION_PRIVATE, "private account")
        return parse_profile(profile, author)

    def fetch_posts(self, profile: CreatorProfile, spec: Dict[str, Any]) -> List[Post]:
        account_value = _account_value(profile, spec)
        collected: List[Post] = []
        for endpoint in spec["posts"]:
            cursor: Optional[str] = None
            fetched_here = 0
            while fetched_here < POST_CAP:
                params = {spec["param"]: account_value, **endpoint.get("extra", {})}
                if endpoint["cursor_param"] and cursor:
                    params[endpoint["cursor_param"]] = cursor
                response = self._call(endpoint["path"], params)
                data = response.get("data") or {}
                items = data.get("items") or []
                if not items:
                    break
                for item in items:
                    collected.append(parse_post(profile.platform, profile, item, endpoint["default_type"]))
                    fetched_here += 1
                    if fetched_here >= POST_CAP:
                        break
                pagination = data.get("pagination") or {}
                cursor = pagination.get("next_cursor")
                has_more = pagination.get("has_more", bool(cursor))
                if not endpoint["cursor_param"] or not has_more or not cursor:
                    break
        return collected

    def _work(
        self,
        profile: CreatorProfile,
        platform: str,
        spec: Dict[str, Any],
        repo: Optional[DataRepo],
        metrics: Optional[RawMetrics] = None,
    ) -> RawMetrics:
        if metrics is None:
            metrics = self.fetch_profile(profile, spec)
        if metrics.resolution_status != RESOLUTION_OK:
            return metrics

        try:
            fetched_posts = self.fetch_posts(profile, spec)
        except Exception as exc:  # noqa: BLE001 - posts failing must not drop the profile row
            log.warning("%s posts fetch failed for %s: %s", platform, profile.handle, exc)
            fetched_posts = []

        if repo is not None:
            existing = load_posts(repo, platform, profile.profile_url)
            posts = merge_and_cap(existing, fetched_posts)
            save_posts(repo, platform, profile.profile_url, posts)
        else:
            posts = merge_and_cap([], fetched_posts)

        if posts:
            metrics.posts_source = "socialcrawl"
            with_likes = [p for p in posts if p.likes is not None]
            with_views = [p for p in posts if p.views is not None]
            with_comments = [p for p in posts if p.comments is not None]
            if with_likes:
                metrics.avg_likes = sum(p.likes for p in with_likes) / len(with_likes)
            if with_comments:
                metrics.avg_comments = sum(p.comments for p in with_comments) / len(with_comments)
            if with_views:
                metrics.avg_views = sum(p.views for p in with_views) / len(with_views)
        return metrics

    def fetch(
        self, profiles: Sequence[CreatorProfile], platform: str, repo: Optional[DataRepo] = None
    ) -> List[RawMetrics]:
        spec = PLATFORM_ENDPOINTS[platform]
        profiles = list(profiles)
        if not profiles:
            return []

        # Profiles: bulk for handle-keyed platforms (1 call per 50 accounts
        # instead of 1 per account) when explicitly enabled, per-account
        # otherwise (off by default -- see Config.socialcrawl_use_bulk_profiles).
        use_bulk = self.config.socialcrawl_use_bulk_profiles and platform in PRISM_BULK_PLATFORMS
        bulk_profiles = self.fetch_profiles_bulk(profiles, platform) if use_bulk else {}

        max_workers = max(1, min(self.config.socialcrawl_max_concurrency, SOCIALCRAWL_MAX_CONCURRENCY_CEILING, len(profiles)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            metrics = list(
                pool.map(lambda p: self._work(p, platform, spec, repo, bulk_profiles.get(p.profile_url)), profiles)
            )
        log.info("%s: %d SocialCrawl credits used this run", platform, self.credits_used)
        return metrics
