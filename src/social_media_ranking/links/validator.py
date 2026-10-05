"""Classify and validate bio-link URLs (section 3).

A URL goes through three steps: normalize (lowercase host, drop tracking
params, resolve shorteners), classify by domain, then check the path against
that platform's profile pattern so content links like a shared video are
rejected rather than scraped as if they were channels.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Outcome of validating a single URL.
ACCEPTED = "accepted"
REJECTED = "rejected"
IGNORED = "ignored"  # Not a social link at all (merch store, newsletter, ...).

DOMAIN_PLATFORMS: Dict[str, str] = {
    "instagram.com": "instagram",
    "instagr.am": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "fb.me": "facebook",
    "fb.watch": "facebook",
    "threads.net": "threads",
    "threads.com": "threads",
    "linkedin.com": "linkedin",
    "lnkd.in": "linkedin",
    "tiktok.com": "tiktok",
    "x.com": "x",
    "twitter.com": "x",
}

# Hosts that hide the real destination; resolve the redirect before classifying.
SHORTENER_HOSTS = {
    "youtu.be",
    "fb.watch",
    "fb.me",
    "lnkd.in",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "ow.ly",
    "rb.gy",
    "cutt.ly",
}

# Query keys that carry no routing meaning and are safe to strip.
TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z_]+|fbclid|gclid|igshid|igsh|si|ref|ref_src|ref_url|mibextid|"
    r"_rdc|_rdr|feature|app|is_from_webapp|sender_device|source|share_id|"
    r"trk|trkinfo|original_referer|originalsubdomain|hl|locale|lang)$",
    re.IGNORECASE,
)

_SUBDOMAIN_NOISE = re.compile(r"^(www|m|mobile|web|[a-z]{2}|[a-z]{2}-[a-z]{2})\.")

YOUTUBE_CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")
HANDLE_SAFE = re.compile(r"^[\w.\-]{1,64}$")
INSTAGRAM_USERNAME = re.compile(r"^[a-z0-9._]{1,30}$", re.IGNORECASE)
TIKTOK_USERNAME = re.compile(r"^[a-z0-9._]{1,24}$", re.IGNORECASE)
X_USERNAME = re.compile(r"^[a-z0-9_]{1,15}$", re.IGNORECASE)

# Path heads that are site features, never creator profiles.
RESERVED_PATHS: Dict[str, set] = {
    "youtube": {
        "watch", "shorts", "playlist", "embed", "live", "results", "feed",
        "post", "community", "hashtag", "gaming", "premium", "about", "t",
        "account", "redirect", "oembed", "s",
    },
    "instagram": {
        "p", "reel", "reels", "tv", "stories", "explore", "direct", "accounts",
        "about", "developer", "legal", "challenge", "s", "sharer", "web",
        "privacy", "terms", "emails", "invites", "topics",
    },
    "facebook": {
        "posts", "videos", "video", "photo", "photos", "watch", "groups",
        "events", "marketplace", "gaming", "sharer", "share", "dialog",
        "login", "help", "policies", "story.php", "permalink.php", "reel",
        "media", "notes", "hashtag", "l.php", "plugins", "people", "search",
    },
    "threads": {"post", "t", "search", "settings", "login", "explore"},
    "linkedin": {
        "posts", "pulse", "feed", "jobs", "learning", "events", "groups",
        "showcase", "help", "legal", "sales", "talent", "sharearticle",
    },
    "tiktok": {
        "video", "tag", "music", "discover", "foryou", "following", "live",
        "explore", "upload", "search", "effect", "embed", "t",
    },
    "x": {
        "home", "explore", "notifications", "messages", "i", "search",
        "settings", "compose", "intent", "hashtag", "share", "login",
        "signup", "tos", "privacy", "about",
    },
}


@dataclass
class LinkCandidate:
    """A raw (creator, url) pair as scraped from a bio-link page."""

    creator_name: str
    raw_url: str


@dataclass
class ValidationResult:
    """What the validator decided about one URL."""

    creator_name: str
    raw_url: str
    normalized_url: str = ""
    platform: str = ""
    handle: str = ""
    platform_id: str = ""
    outcome: str = IGNORED
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.outcome == ACCEPTED


def _host_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if "@" in host:
        host = host.split("@", 1)[-1]
    return host.split(":", 1)[0]


def registrable_domain(url: str) -> str:
    """Host with locale/mobile subdomains stripped, e.g. ``uk.linkedin.com`` -> ``linkedin.com``."""
    host = _host_of(url)
    while True:
        stripped = _SUBDOMAIN_NOISE.sub("", host, count=1)
        if stripped == host or stripped.count(".") < 1:
            break
        host = stripped
    return host


def normalize_url(url: str) -> str:
    """Lowercase the host, force https, drop tracking params and trailing slash.

    Case-insensitive host matching and param stripping both happen here so
    every downstream rule can assume a canonical form.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if not urlsplit(url).scheme:
        url = "https://" + url.lstrip("/")
    parts = urlsplit(url)
    host = _host_of(url)
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    )
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    return urlunsplit(("https", host, path, query, ""))


def _path_segments(url: str) -> List[str]:
    return [seg for seg in urlsplit(url).path.split("/") if seg]


def classify_platform(url: str) -> str:
    """Map a URL to a platform by domain, or ``""`` when it is not a social link."""
    domain = registrable_domain(url)
    if domain in DOMAIN_PLATFORMS:
        return DOMAIN_PLATFORMS[domain]
    # Catch regional hosts such as linkedin.com.cn that survive noise stripping.
    for known, platform in DOMAIN_PLATFORMS.items():
        if domain.endswith("." + known):
            return platform
    return ""


def is_shortener(url: str) -> bool:
    host = _host_of(url)
    return host in SHORTENER_HOSTS or registrable_domain(url) in SHORTENER_HOSTS


def _reject(result: ValidationResult, reason: str) -> ValidationResult:
    result.outcome = REJECTED
    result.reason = reason
    return result


def _accept(result: ValidationResult, handle: str, platform_id: str = "") -> ValidationResult:
    result.outcome = ACCEPTED
    result.handle = handle
    result.platform_id = platform_id
    result.reason = ""
    return result


def _validate_youtube(url: str, result: ValidationResult) -> ValidationResult:
    segments = _path_segments(url)
    query = dict(parse_qsl(urlsplit(url).query))
    if not segments:
        return _reject(result, "youtube: no channel path")
    head = segments[0].lower()
    if head in RESERVED_PATHS["youtube"] or "v" in query:
        return _reject(result, f"youtube: content path /{head}")
    if head == "channel":
        if len(segments) < 2 or not YOUTUBE_CHANNEL_ID.match(segments[1]):
            return _reject(result, "youtube: malformed channel id")
        return _accept(result, segments[1], platform_id=segments[1])
    if segments[0].startswith("@"):
        handle = segments[0]
        if len(segments) > 1 and segments[1].lower() not in {"featured", "videos", "about"}:
            return _reject(result, f"youtube: content path /{segments[1]}")
        return _accept(result, handle)
    if head in {"c", "user"}:
        if len(segments) < 2:
            return _reject(result, f"youtube: /{head} with no name")
        return _accept(result, segments[1])
    return _reject(result, f"youtube: unrecognized path /{head}")


def _validate_single_segment(url: str, result: ValidationResult, platform: str, pattern: re.Pattern) -> ValidationResult:
    segments = _path_segments(url)
    if not segments:
        return _reject(result, f"{platform}: no username in path")
    head = segments[0]
    if head.lower() in RESERVED_PATHS[platform]:
        return _reject(result, f"{platform}: content path /{head.lower()}")
    if len(segments) > 1:
        return _reject(result, f"{platform}: extra path segment /{segments[1]}")
    username = head[1:] if head.startswith("@") else head
    if not pattern.match(username):
        return _reject(result, f"{platform}: invalid username '{head}'")
    return _accept(result, username)


def _validate_instagram(url: str, result: ValidationResult) -> ValidationResult:
    return _validate_single_segment(url, result, "instagram", INSTAGRAM_USERNAME)


def _validate_tiktok(url: str, result: ValidationResult) -> ValidationResult:
    segments = _path_segments(url)
    if not segments:
        return _reject(result, "tiktok: no username in path")
    if not segments[0].startswith("@"):
        return _reject(result, f"tiktok: /{segments[0].lower()} is not an @username")
    return _validate_single_segment(url, result, "tiktok", TIKTOK_USERNAME)


def _validate_threads(url: str, result: ValidationResult) -> ValidationResult:
    segments = _path_segments(url)
    if not segments:
        return _reject(result, "threads: no username in path")
    if not segments[0].startswith("@"):
        return _reject(result, f"threads: /{segments[0].lower()} is not an @username")
    return _validate_single_segment(url, result, "threads", HANDLE_SAFE)


def _validate_facebook(url: str, result: ValidationResult) -> ValidationResult:
    segments = _path_segments(url)
    query = dict(parse_qsl(urlsplit(url).query))
    if not segments:
        return _reject(result, "facebook: no page in path")
    head = segments[0].lower()
    if head == "profile.php":
        page_id = query.get("id", "")
        if not page_id.isdigit():
            return _reject(result, "facebook: profile.php without numeric id")
        return _accept(result, page_id, platform_id=page_id)
    if head in RESERVED_PATHS["facebook"]:
        return _reject(result, f"facebook: content path /{head}")
    if head == "pages":
        # /pages/<name>/<id> is the legacy page URL shape.
        if len(segments) >= 3 and segments[2].isdigit():
            return _accept(result, segments[1], platform_id=segments[2])
        return _reject(result, "facebook: malformed /pages/ url")
    if len(segments) > 1:
        return _reject(result, f"facebook: extra path segment /{segments[1]}")
    if not HANDLE_SAFE.match(head):
        return _reject(result, f"facebook: invalid page name '{segments[0]}'")
    return _accept(result, segments[0])


def _validate_x(url: str, result: ValidationResult) -> ValidationResult:
    return _validate_single_segment(url, result, "x", X_USERNAME)


def _validate_linkedin(url: str, result: ValidationResult) -> ValidationResult:
    segments = _path_segments(url)
    if not segments:
        return _reject(result, "linkedin: no profile in path")
    head = segments[0].lower()
    if head in RESERVED_PATHS["linkedin"]:
        return _reject(result, f"linkedin: content path /{head}")
    if head not in {"in", "company", "school"}:
        return _reject(result, f"linkedin: unrecognized path /{head}")
    if len(segments) < 2 or not HANDLE_SAFE.match(segments[1]):
        return _reject(result, f"linkedin: /{head} with no valid name")
    if len(segments) > 2 and segments[2].lower() in RESERVED_PATHS["linkedin"]:
        return _reject(result, f"linkedin: content path /{segments[2].lower()}")
    return _accept(result, f"{head}/{segments[1]}")


_VALIDATORS: Dict[str, Callable[[str, ValidationResult], ValidationResult]] = {
    "youtube": _validate_youtube,
    "instagram": _validate_instagram,
    "facebook": _validate_facebook,
    "threads": _validate_threads,
    "linkedin": _validate_linkedin,
    "tiktok": _validate_tiktok,
    "x": _validate_x,
}


def validate_url(
    raw_url: str,
    creator_name: str = "",
    resolver: Optional[Callable[[str], str]] = None,
) -> ValidationResult:
    """Normalize, classify, and validate one URL.

    ``resolver`` expands shortened URLs; pass ``None`` to skip network calls and
    treat unexpanded shorteners as unresolvable.
    """
    result = ValidationResult(creator_name=creator_name, raw_url=raw_url)
    normalized = normalize_url(raw_url)
    if not normalized:
        return _reject(result, "empty url")

    if is_shortener(normalized):
        if resolver is None:
            result.normalized_url = normalized
            return _reject(result, "shortened url not resolved")
        expanded = normalize_url(resolver(normalized))
        if expanded and not is_shortener(expanded):
            normalized = expanded
        elif _host_of(normalized) not in {"youtu.be", "fb.watch"}:
            result.normalized_url = normalized
            return _reject(result, "shortener did not resolve")

    result.normalized_url = normalized
    platform = classify_platform(normalized)
    if not platform:
        result.outcome = IGNORED
        result.reason = "not a social platform link"
        return result

    result.platform = platform
    validated = _VALIDATORS[platform](normalized, result)
    if validated.accepted:
        validated.normalized_url = canonical_profile_url(platform, validated.handle, normalized)
    return validated


def canonical_profile_url(platform: str, handle: str, fallback: str = "") -> str:
    """One stable URL per profile so trailing-slash and case variants dedupe."""
    if platform == "instagram":
        return f"https://instagram.com/{handle.lower()}"
    if platform == "tiktok":
        return f"https://tiktok.com/@{handle.lower()}"
    if platform == "threads":
        return f"https://threads.net/@{handle.lower()}"
    if platform == "linkedin":
        return f"https://linkedin.com/{handle.lower()}"
    if platform == "facebook":
        return f"https://facebook.com/{handle.lower()}"
    if platform == "youtube":
        if YOUTUBE_CHANNEL_ID.match(handle):
            return f"https://youtube.com/channel/{handle}"
        if handle.startswith("@"):
            return f"https://youtube.com/{handle.lower()}"
        return f"https://youtube.com/c/{handle.lower()}"
    return fallback


def validate_candidates(
    candidates: Iterable[LinkCandidate],
    resolver: Optional[Callable[[str], str]] = None,
) -> List[ValidationResult]:
    """Validate many URLs, deduping accepted profiles per creator."""
    results: List[ValidationResult] = []
    seen: set = set()
    for candidate in candidates:
        result = validate_url(candidate.raw_url, candidate.creator_name, resolver=resolver)
        if result.accepted:
            key = (result.creator_name.strip().lower(), result.platform, result.normalized_url)
            if key in seen:
                continue
            seen.add(key)
        results.append(result)
    return results
