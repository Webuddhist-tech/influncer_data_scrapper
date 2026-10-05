"""YouTube Data API v3 extractor (section 5).

Uses the official API rather than SocialCrawl credits. Calls cost one quota
unit regardless of batch size, so IDs are packed 50-per-call and resolved
handles are cached back into creator_profiles.csv. Posts are paginated via
playlistItems up to the 90-day / 100-post cap (section 5) and cached per
account (posts_cache.py) so a rerun only fetches what's new.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Sequence

from ..config import Config, POST_CAP, YOUTUBE_MAX_IDS_PER_CALL
from ..http import HttpClient
from ..models import RESOLUTION_ERROR, RESOLUTION_NOT_FOUND, RESOLUTION_OK, CreatorProfile, Post, RawMetrics
from ..posts_cache import load_posts, merge_and_cap, parse_timestamp, save_posts, window_cutoff
from ..storage import DataRepo, utc_now
from .socialcrawl import chunk

log = logging.getLogger(__name__)

_ISO8601_DURATION = re.compile(r"^P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
SHORT_MAX_SECONDS = 60


def parse_iso8601_duration(value: Optional[str]) -> Optional[int]:
    """``PT4M13S`` -> 253. Returns None for anything that doesn't parse."""
    if not value:
        return None
    match = _ISO8601_DURATION.match(value)
    if not match:
        return None
    hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def classify_video_type(duration_s: Optional[int]) -> str:
    if duration_s is None:
        return "other"
    return "short" if duration_s <= SHORT_MAX_SECONDS else "video"


def _int_or_none(value: Optional[str]) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class YouTubeExtractor:
    def __init__(self, config: Config, client: Optional[HttpClient] = None):
        self.config = config
        self.client = client or HttpClient(config.request_timeout, config.max_retries)

    def _get(self, endpoint: str, **params: object) -> Dict:
        params = {**params, "key": self.config.youtube_api_key}
        return self.client.get_json(f"{self.config.youtube_base_url}/{endpoint}", params=params)

    def resolve_handle(self, handle: str) -> str:
        """Turn an ``@handle`` into a stable channel ID (cache the result)."""
        payload = self._get("channels", part="id", forHandle=handle if handle.startswith("@") else f"@{handle}")
        items = payload.get("items") or []
        return items[0]["id"] if items else ""

    def resolve_channel_ids(self, profiles: Sequence[CreatorProfile]) -> Dict[str, str]:
        """Map profile key -> channel ID, resolving handles only when uncached."""
        resolved: Dict[str, str] = {}
        for profile in profiles:
            if profile.platform_id:
                resolved[profile.key] = profile.platform_id
                continue
            handle = profile.handle
            if handle.startswith("UC") and len(handle) == 24:
                resolved[profile.key] = handle
                continue
            try:
                channel_id = self.resolve_handle(handle)
            except Exception as exc:  # noqa: BLE001
                log.warning("handle resolution failed for %s: %s", handle, exc)
                channel_id = ""
            if channel_id:
                resolved[profile.key] = channel_id
                profile.platform_id = channel_id
        return resolved

    def fetch_channels(self, channel_ids: Sequence[str]) -> Dict[str, Dict]:
        """channels.list batched 50 IDs per call."""
        out: Dict[str, Dict] = {}
        for batch in chunk(list(channel_ids), YOUTUBE_MAX_IDS_PER_CALL):
            payload = self._get(
                "channels",
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
                maxResults=YOUTUBE_MAX_IDS_PER_CALL,
            )
            for item in payload.get("items", []):
                out[item["id"]] = item
        return out

    def fetch_recent_video_ids(self, uploads_playlist_id: str, cutoff, cap: int = POST_CAP) -> List[str]:
        """Page through uploads until the cap or the 90-day cutoff, whichever first."""
        ids: List[str] = []
        page_token: Optional[str] = None
        while len(ids) < cap:
            params = dict(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=min(50, cap - len(ids)),
            )
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("playlistItems", **params)
            items = payload.get("items", [])
            if not items:
                break
            stop = False
            for item in items:
                details = item.get("contentDetails", {})
                published = parse_timestamp(details.get("videoPublishedAt", ""))
                if published is not None and published < cutoff:
                    stop = True
                    break
                ids.append(details["videoId"])
                if len(ids) >= cap:
                    break
            page_token = payload.get("nextPageToken")
            if stop or not page_token:
                break
        return ids

    def fetch_video_stats(self, video_ids: Sequence[str]) -> List[Dict]:
        """videos.list batched 50 IDs per call."""
        stats: List[Dict] = []
        for batch in chunk(list(video_ids), YOUTUBE_MAX_IDS_PER_CALL):
            payload = self._get("videos", part="snippet,statistics,contentDetails", id=",".join(batch))
            stats.extend(payload.get("items", []))
        return stats

    def _build_posts(self, profile: CreatorProfile, videos: Sequence[Dict]) -> List[Post]:
        posts: List[Post] = []
        for video in videos:
            snippet = video.get("snippet", {})
            statistics = video.get("statistics", {})
            duration_s = parse_iso8601_duration(video.get("contentDetails", {}).get("duration"))
            thumbnails = snippet.get("thumbnails", {}) or {}
            thumbnail = (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url")
            title = str(snippet.get("title") or "")
            posts.append(
                Post(
                    platform="youtube",
                    profile_url=profile.profile_url,
                    id=video.get("id", ""),
                    url=f"https://www.youtube.com/watch?v={video.get('id', '')}",
                    published_at=str(snippet.get("publishedAt") or ""),
                    type=classify_video_type(duration_s),
                    title=title[:140],
                    thumbnail_url=thumbnail,
                    duration_s=duration_s,
                    views=_int_or_none(statistics.get("viewCount")),
                    likes=_int_or_none(statistics.get("likeCount")),
                    # Comments can be disabled on a video; the field is then absent, not 0.
                    comments=_int_or_none(statistics.get("commentCount")) if "commentCount" in statistics else None,
                    shares=None,  # YouTube's API never exposes a share count.
                )
            )
        return posts

    def fetch(self, profiles: Sequence[CreatorProfile], repo: Optional[DataRepo] = None) -> List[RawMetrics]:
        ids_by_profile = self.resolve_channel_ids(profiles)
        try:
            channels = self.fetch_channels(sorted(set(ids_by_profile.values())))
        except Exception as exc:  # noqa: BLE001
            log.error("channels.list failed: %s", exc)
            channels = {}

        cutoff = window_cutoff()

        # One videos.list call can cover several channels, so pool the IDs,
        # but only fetch what's new since each account's cache cutoff.
        uploads_by_channel: Dict[str, List[str]] = {}
        for channel_id, item in channels.items():
            playlist_id = (item.get("contentDetails", {}).get("relatedPlaylists", {}) or {}).get("uploads")
            if not playlist_id:
                continue
            try:
                uploads_by_channel[channel_id] = self.fetch_recent_video_ids(playlist_id, cutoff)
            except Exception as exc:  # noqa: BLE001
                log.warning("uploads playlist failed for %s: %s", channel_id, exc)

        all_video_ids = [vid for ids in uploads_by_channel.values() for vid in ids]
        try:
            video_items = {v["id"]: v for v in self.fetch_video_stats(all_video_ids)}
        except Exception as exc:  # noqa: BLE001
            log.warning("videos.list failed: %s", exc)
            video_items = {}

        metrics: List[RawMetrics] = []
        for profile in profiles:
            channel_id = ids_by_profile.get(profile.key, "")
            item = channels.get(channel_id)
            if not item:
                metrics.append(
                    RawMetrics(
                        creator_name=profile.creator_name,
                        platform="youtube",
                        handle=profile.handle,
                        profile_url=profile.profile_url,
                        last_updated_at=utc_now(),
                        resolution_status=RESOLUTION_NOT_FOUND if channel_id else RESOLUTION_ERROR,
                        raw_snippet="channel not returned by channels.list",
                    )
                )
                continue

            statistics = item.get("statistics", {})
            videos = [video_items[vid] for vid in uploads_by_channel.get(channel_id, []) if vid in video_items]
            fetched_posts = self._build_posts(profile, videos)

            if repo is not None:
                existing = load_posts(repo, "youtube", profile.profile_url)
                posts = merge_and_cap(existing, fetched_posts)
                save_posts(repo, "youtube", profile.profile_url, posts)
            else:
                posts = merge_and_cap([], fetched_posts)

            avg_views = avg_likes = avg_comments = None
            with_engagement = [p for p in posts if p.views is not None or p.likes is not None]
            if with_engagement:
                avg_views = sum(p.views or 0 for p in with_engagement) / len(with_engagement)
                avg_likes = sum(p.likes or 0 for p in with_engagement) / len(with_engagement)
                avg_comments = sum(p.comments or 0 for p in with_engagement) / len(with_engagement)

            metrics.append(
                RawMetrics(
                    creator_name=profile.creator_name,
                    platform="youtube",
                    handle=profile.handle,
                    profile_url=profile.profile_url,
                    followers=_int_or_none(statistics.get("subscriberCount")),
                    post_count=_int_or_none(statistics.get("videoCount")),
                    total_views=_int_or_none(statistics.get("viewCount")),
                    avg_views=avg_views,
                    avg_likes=avg_likes,
                    avg_comments=avg_comments,
                    account_created_at=str(item.get("snippet", {}).get("publishedAt") or ""),
                    last_updated_at=utc_now(),
                    resolution_status=RESOLUTION_OK,
                    followers_source="youtube_api",
                    posts_source="youtube_api",
                    raw_snippet=str(statistics)[:500],
                )
            )
        return metrics
