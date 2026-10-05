"""Configuration: platforms, weights, batch sizes, credentials.

Anything a provider could change (batch limits, API hosts) is read from the
environment so it never has to be edited in code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Tuple

PLATFORMS: Tuple[str, ...] = (
    "youtube",
    "instagram",
    "facebook",
    "tiktok",
    "threads",
    "x",
    "linkedin",
)

# Platforms fetched through SocialCrawl; YouTube uses its own official API.
SOCIALCRAWL_PLATFORMS: Tuple[str, ...] = (
    "instagram",
    "facebook",
    "tiktok",
    "threads",
    "x",
    "linkedin",
)

# Section 8 — suggested platform weights for the total creator score.
PLATFORM_WEIGHTS: Dict[str, float] = {
    "youtube": 0.30,
    "instagram": 0.25,
    "facebook": 0.15,
    "tiktok": 0.15,
    "linkedin": 0.10,
    "threads": 0.05,
}

# Section 8 — component split inside a single platform score.
REACH_WEIGHT = 0.50
ENGAGEMENT_WEIGHT = 0.35
GROWTH_WEIGHT = 0.15

# YouTube Data API v3 hard limits.
YOUTUBE_MAX_IDS_PER_CALL = 50

# Section 5 — post collection window shared by every platform's post fetch.
POST_WINDOW_DAYS = 90
POST_CAP = 100

# SocialCrawl real limits (confirmed in docs/socialcrawl-fields.md): 50
# concurrent requests and 600/minute per key — there is no multi-account
# batch endpoint, so this bounds a thread pool, not a request body size.
SOCIALCRAWL_MAX_CONCURRENCY_CEILING = 50
SOCIALCRAWL_RATE_LIMIT_PER_MINUTE = 600
# Section 4: "max 4 tries" on 429/5xx, independent of the pipeline's generic
# HTTP retry count.
SOCIALCRAWL_MAX_RETRIES = 4


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Config:
    """Runtime configuration, assembled from the environment."""

    socialcrawl_api_key: str = ""
    socialcrawl_base_url: str = "https://www.socialcrawl.dev/v1"
    # Section 4: concurrent in-flight requests, capped at the documented
    # ceiling regardless of what's configured.
    socialcrawl_max_concurrency: int = SOCIALCRAWL_MAX_CONCURRENCY_CEILING
    # Off by default (2026-10-05, user request): Prism bulk profiles turned
    # out costlier/less predictable in practice than the per-account path on
    # a small credit budget. The code stays correct and tested
    # (fetch_profiles_bulk); this just stops `fetch()` from calling it
    # automatically until that's revisited.
    socialcrawl_use_bulk_profiles: bool = False
    youtube_api_key: str = ""
    youtube_base_url: str = "https://www.googleapis.com/youtube/v3"
    request_timeout: int = 30
    max_retries: int = 3
    platform_weights: Dict[str, float] = field(default_factory=lambda: dict(PLATFORM_WEIGHTS))
    # The roster source: a published Google Sheet's CSV export URL (same
    # mechanism as Desktop/ranking's extract.py), downloaded fresh on every
    # extract-links run. Empty means "use whatever --input points at instead"
    # (a committed .xlsx/.csv) -- see README "Creator roster source".
    creator_sheet_csv_url: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            socialcrawl_api_key=os.environ.get("SOCIALCRAWL_API_KEY", ""),
            socialcrawl_base_url=os.environ.get(
                "SOCIALCRAWL_BASE_URL", "https://www.socialcrawl.dev/v1"
            ),
            socialcrawl_max_concurrency=min(
                _env_int("SOCIALCRAWL_MAX_CONCURRENCY", SOCIALCRAWL_MAX_CONCURRENCY_CEILING),
                SOCIALCRAWL_MAX_CONCURRENCY_CEILING,
            ),
            socialcrawl_use_bulk_profiles=os.environ.get("SOCIALCRAWL_USE_BULK_PROFILES", "").lower() == "true",
            youtube_api_key=os.environ.get("YOUTUBE_API_KEY", ""),
            youtube_base_url=os.environ.get(
                "YOUTUBE_BASE_URL", "https://www.googleapis.com/youtube/v3"
            ),
            request_timeout=_env_int("SOCIAL_MEDIA_RANKING_REQUEST_TIMEOUT", 30),
            max_retries=_env_int("SOCIAL_MEDIA_RANKING_MAX_RETRIES", 3),
            creator_sheet_csv_url=os.environ.get("CREATOR_SHEET_CSV_URL", ""),
        )
