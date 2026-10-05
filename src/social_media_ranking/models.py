"""Row shapes that move between pipeline stages.

Each dataclass knows how to round-trip itself through a CSV row so every stage
can be re-run independently from files on disk.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional

# creator_profiles.csv status values.
STATUS_RESOLVED = "resolved"
STATUS_PENDING = "pending"
STATUS_INVALID = "invalid"

# Brief section 3 — only this confirmation value reaches the public export.
CONFIRMATION_CONFIRMED = "Confirmed"
CONFIRMATION_DEFAULT = "Pending"

# Per-fetch outcome, stored alongside every raw record (section 7).
RESOLUTION_OK = "ok"
RESOLUTION_RATE_LIMITED = "rate-limited"
RESOLUTION_PRIVATE = "private"
RESOLUTION_NOT_FOUND = "not-found"
RESOLUTION_ERROR = "error"


def _coerce(value: Any, target: Any) -> Any:
    if value is None or value == "":
        return None
    if target is bool:
        return str(value).strip().lower() in {"1", "true", "yes"}
    if target is int:
        return int(float(value))
    if target is float:
        return float(value)
    return value


@dataclass
class CreatorProfile:
    """One (creator, platform) pair in data/creator_profiles.csv."""

    creator_name: str
    platform: str
    handle: str
    profile_url: str
    status: str = STATUS_PENDING
    source_url: str = ""
    reason: str = ""
    # Cached YouTube channel ID: handles change, IDs do not (section 5).
    platform_id: str = ""
    # Per-creator fields (section 3), duplicated onto every platform row for
    # this creator_name rather than a separate creators.json — see
    # overrides.apply_creator_meta, which is the only place that sets them.
    confirmation: str = CONFIRMATION_DEFAULT
    # Derived once by overrides.assign_slugs and persisted here afterward, so
    # it never changes between runs (brief section 3).
    slug: str = ""
    merged_into: str = ""
    language: str = ""
    # Scraped straight from the Linktree page's own embedded data (its one
    # reliably-hosted photo, regardless of the creator's platform choices).
    # An overrides.json avatar_url still wins over this — see overrides.py.
    avatar_url: str = ""
    # Sheet-sourced contact info, for organizers only (section 2: these never
    # leave this repo — export_public.py's allowlist never references them).
    email: str = ""
    whatsapp: str = ""

    def to_row(self) -> Dict[str, str]:
        return {k: ("" if v is None else str(v)) for k, v in asdict(self).items()}

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "CreatorProfile":
        names = {f.name for f in fields(cls)}
        return cls(**{k: (v or "") for k, v in row.items() if k in names})

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.profile_url}"


@dataclass
class RawMetrics:
    """A week's raw pull for one profile, written to data/raw/{platform}/."""

    creator_name: str
    platform: str
    handle: str
    profile_url: str
    followers: Optional[int] = None
    following: Optional[int] = None
    post_count: Optional[int] = None
    total_views: Optional[int] = None
    total_likes: Optional[int] = None
    avg_likes: Optional[float] = None
    avg_comments: Optional[float] = None
    avg_views: Optional[float] = None
    # Provider-computed engagement rate, preferred over our own math when present.
    provider_engagement_rate: Optional[float] = None
    verified: Optional[bool] = None
    category: str = ""
    account_created_at: str = ""
    last_post_at: str = ""
    last_updated_at: str = ""
    resolution_status: str = RESOLUTION_OK
    raw_snippet: str = ""
    # Section 4/5 — which resolver answered, so a creator's export can show it.
    followers_source: str = ""
    posts_source: str = ""

    def to_row(self) -> Dict[str, str]:
        return {k: ("" if v is None else str(v)) for k, v in asdict(self).items()}

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "RawMetrics":
        typed = {f.name: f.type for f in fields(cls)}
        kwargs: Dict[str, Any] = {}
        for name, annotation in typed.items():
            if name not in row:
                continue
            text = str(annotation)
            if "int" in text:
                kwargs[name] = _coerce(row[name], int)
            elif "float" in text:
                kwargs[name] = _coerce(row[name], float)
            elif "bool" in text:
                kwargs[name] = _coerce(row[name], bool)
            else:
                kwargs[name] = row[name] or ""
        return cls(**kwargs)


@dataclass
class PlatformScore:
    """Scored result for one (creator, platform) in data/scores/{date}.csv."""

    creator_name: str
    platform: str
    handle: str
    followers: Optional[int] = None
    reach: float = 0.0
    engagement: float = 0.0
    growth: float = 0.0
    score: float = 0.0
    # True when this row was carried over from a previous week's good data.
    carried_over: bool = False

    def to_row(self) -> Dict[str, str]:
        return {k: ("" if v is None else str(v)) for k, v in asdict(self).items()}


POST_TYPES = ("video", "short", "image", "carousel", "text", "other")


@dataclass
class Post:
    """One post in the last 90 days (section 5). Cap 100 per account.

    Missing fields are ``None``, never ``0`` — a public value of zero and an
    unavailable field mean different things.
    """

    platform: str
    profile_url: str
    id: str
    url: str
    published_at: str
    type: str = "other"
    title: str = ""
    thumbnail_url: Optional[str] = None
    duration_s: Optional[int] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None

    def to_row(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Post":
        names = {f.name for f in fields(cls)}
        ints = {"duration_s", "views", "likes", "comments", "shares"}
        kwargs: Dict[str, Any] = {}
        for key, value in row.items():
            if key not in names:
                continue
            kwargs[key] = _coerce(value, int) if key in ints else value
        return cls(**kwargs)


@dataclass
class CreatorScore:
    """Cross-platform rollup for one creator."""

    creator_name: str
    total_score: float = 0.0
    resolved_platforms: int = 0
    platform_scores: Dict[str, float] = field(default_factory=dict)
    coverage: str = "0/6"
