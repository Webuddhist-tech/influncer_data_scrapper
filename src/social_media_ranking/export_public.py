"""Public export (brief section 7) — the only thing allowed to leave this
repo. Every field below is picked by hand from known-safe sources
(creator_profiles.csv, data/history/*.csv, the post cache, this week's raw
snapshot, overrides.json's bio/avatar_url) — nothing from the roster sheet,
email, WhatsApp, or notes ever reaches this module, which is itself the
allowlist the brief asks for.

Only ``confirmation == "Confirmed"`` creators are included (section 3), and
only once ``weekly_score.score_week`` has already run for this week (its
``CreatorWeekScore`` results are passed in, not recomputed here).
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import PLATFORMS
from .http import HttpClient
from .models import CONFIRMATION_CONFIRMED, RESOLUTION_OK, CreatorProfile
from .module_scoring import MODULE_WEIGHTS, CreatorWeekScore, compute_periods
from .overrides import load_overrides
from .posts_cache import load_posts, parse_timestamp
from .storage import DataRepo, read_csv, week_label, write_json
from .weekly_score import creator_history_totals

log = logging.getLogger(__name__)

MAX_HISTORY_WEEKS = 12
MAX_RECENT_POSTS = 8
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)\+?\d[\d\-\s()]{8,}\d(?!\d)")
_WA_LINK = re.compile(r"wa\.me|whatsapp", re.IGNORECASE)
# A date/timestamp like "2026-09-07" or "2026-09-07T02:00:00Z" reads as a
# long digit run too -- don't let the phone check flag our own week labels.
_ISO_DATE_OR_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$")


def _looks_like_phone(value: str) -> bool:
    if _ISO_DATE_OR_DATETIME.match(value):
        return False
    match = _PHONE.search(value)
    if not match:
        return False
    return len(re.sub(r"\D", "", match.group())) >= 9


def _group_profiles(profiles: Sequence[CreatorProfile]) -> Dict[str, List[CreatorProfile]]:
    out: Dict[str, List[CreatorProfile]] = {}
    for profile in profiles:
        if profile.merged_into:
            continue
        out.setdefault(profile.creator_name, []).append(profile)
    return out


def _verified_lookup(repo: DataRepo, week: str) -> Dict[str, bool]:
    """``"{platform}|{profile_url}" -> verified`` for whoever resolved fresh this week.

    Carried-over accounts don't get a fresh verified read (documented limitation).
    """
    out: Dict[str, bool] = {}
    for platform in PLATFORMS:
        for row in read_csv(repo.raw_file(platform, week)):
            if row.get("resolution_status") != RESOLUTION_OK or row.get("verified") in (None, ""):
                continue
            out[f"{platform}|{row.get('profile_url')}"] = str(row["verified"]).lower() == "true"
    return out


def _recent_posts(repo: DataRepo, creator_rows: Sequence[CreatorProfile], platforms_this_week: Sequence[str]) -> List[Dict[str, Any]]:
    all_posts = []
    for row in creator_rows:
        if row.platform not in platforms_this_week:
            continue
        all_posts.extend(load_posts(repo, row.platform, row.profile_url))
    all_posts.sort(key=lambda p: parse_timestamp(p.published_at) or _EPOCH, reverse=True)
    return [
        {
            "platform": p.platform,
            "url": p.url,
            "title": p.title,
            "type": p.type,
            "published_at": p.published_at or None,
            "thumbnail_url": p.thumbnail_url,
            "views": p.views,
            "likes": p.likes,
            "comments": p.comments,
            "shares": p.shares,
        }
        for p in all_posts[:MAX_RECENT_POSTS]
    ]


def _dedupe_by_platform(history_rows: Any) -> Dict[str, Dict[str, str]]:
    """One history row per platform, preferring a resolved/carried row over
    an unresolved one -- never letting a duplicate link's "didn't resolve"
    row silently hide another link's real data for the same platform.

    A creator can have more than one profile row for the same platform
    (e.g. two YouTube links found on one Linktree page); if only one of
    them actually resolved, a naive {row["platform"]: row for row in rows}
    dict can end up keeping whichever happened to come last in the file --
    sometimes the unresolved one. Found running against real data: a
    creator's real YouTube followers were missing from their export because
    a duplicate, never-resolved YouTube row for the same creator came after
    it in the history file.
    """
    by_platform: Dict[str, Dict[str, str]] = {}
    for row in history_rows:
        platform = row["platform"]
        existing = by_platform.get(platform)
        is_real = row.get("resolved") == "true" or row.get("carried_over") == "true"
        existing_is_real = existing is not None and (existing.get("resolved") == "true" or existing.get("carried_over") == "true")
        if existing is None or (is_real and not existing_is_real):
            by_platform[platform] = row
    return by_platform


def _download_avatar(client: HttpClient, avatar_url: str, dest: Path) -> bool:
    try:
        response = client.request("GET", avatar_url)
        if response.status_code >= 400 or not response.content:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return True
    except Exception as exc:  # noqa: BLE001 - a bad avatar must not fail the whole export
        log.warning("avatar download failed for %s: %s", avatar_url, exc)
        return False


def build_export(
    repo: DataRepo,
    all_profiles: Sequence[CreatorProfile],
    scores: Dict[str, CreatorWeekScore],
    week: Optional[str] = None,
    download_avatars: bool = True,
    avatar_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the in-memory export payload: ``{"summary": {...}, "creators": {slug: {...}}}``.

    ``scores`` is ``weekly_score.score_week``'s return value for this same
    week — this function doesn't rescore, it only selects and shapes fields
    for public consumption.
    """
    week = week or week_label()
    overrides = load_overrides(repo)
    by_creator = _group_profiles(all_profiles)
    verified = _verified_lookup(repo, week)
    client = HttpClient()
    avatar_dir = avatar_dir or (repo.root.parent / "export" / "public" / "avatars")

    summary_creators: List[Dict[str, Any]] = []
    creator_pages: Dict[str, Dict[str, Any]] = {}

    for creator_name, rows in by_creator.items():
        confirmation = next((r.confirmation for r in rows if r.confirmation), "")
        if confirmation != CONFIRMATION_CONFIRMED:
            continue
        result = scores.get(creator_name)
        if result is None or result.total_score is None:
            continue  # not resolved/carried anywhere, or nothing scoreable this week
        slug = next((r.slug for r in rows if r.slug), "")
        if not slug:
            log.warning("skipping %s: confirmed but has no slug yet (run validate-links first)", creator_name)
            continue

        history_rows = _dedupe_by_platform(
            r for r in read_csv(repo.history_file(week)) if r.get("creator_name") == creator_name
        )

        meta = overrides.get(creator_name, {})
        language = meta.get("language") or next((r.language for r in rows if r.language), "") or None
        bio = meta.get("bio", "")
        # apply_creator_meta already resolved override-vs-scraped onto every
        # row at extract/validate time; the overrides.json lookup here is
        # just a safety net if export-public runs before that happens.
        avatar_url = meta.get("avatar_url") or next((r.avatar_url for r in rows if r.avatar_url), None)

        platform_block: Dict[str, Any] = {}
        carried_platforms: List[str] = []
        for row in rows:
            match = history_rows.get(row.platform)
            if match is None or (match.get("resolved") != "true" and match.get("carried_over") != "true"):
                continue
            carried = match.get("carried_over") == "true"
            if carried:
                carried_platforms.append(row.platform)
            followers = int(match["followers"]) if match.get("followers") not in (None, "") else None
            platform_block[row.platform] = {
                "handle": row.handle,
                "profile_url": row.profile_url,
                "followers": followers,
                "followers_source": match.get("followers_source") or None,
                "verified": verified.get(f"{row.platform}|{row.profile_url}"),
                "engagement_rate": float(match["engagement_rate"]) if match.get("engagement_rate") not in (None, "") else None,
                "posts_90d": int(match["posts_90d"]) if match.get("posts_90d") not in (None, "") else None,
                "carried_over": carried,
            }

        history_pairs = creator_history_totals(repo, creator_name, week)
        periods = compute_periods([score for _, score in history_pairs])
        history_for_page = [{"week": w, "score": s} for w, s in history_pairs][-MAX_HISTORY_WEEKS:]

        avatar_rel = f"avatars/{slug}.jpg" if avatar_url else None
        if avatar_url and download_avatars:
            if not _download_avatar(client, avatar_url, avatar_dir / f"{slug}.jpg"):
                avatar_rel = None

        posts = _recent_posts(repo, rows, list(platform_block.keys()))

        summary_creators.append(
            {
                "slug": slug,
                "name": creator_name,
                "avatar": avatar_rel,
                "language": language,
                "followers": result.total_followers,
                "follower_growth": round(result.follower_growth, 4) if result.follower_growth is not None else None,
                "platforms": sorted(platform_block.keys()),
                "carried_over": sorted(carried_platforms),
                "confidence": result.confidence,
                "scores": periods,
            }
        )

        creator_pages[slug] = {
            "slug": slug,
            "name": creator_name,
            "bio": bio,
            "avatar": avatar_rel,
            "language": language,
            "week": week,
            "confidence": result.confidence,
            "missing_modules": result.missing_modules,
            "modules": result.modules,
            "history": history_for_page,
            "metrics": {
                "engagement_rate": [_platform_median(platform_block, "engagement_rate"), None],
                "posts_per_week": [
                    round(sum(p.get("posts_90d") or 0 for p in platform_block.values()) / (90 / 7.0), 2),
                    None,
                ],
                "follower_growth": [round(result.follower_growth, 4) if result.follower_growth is not None else None, None],
            },
            "platforms": platform_block,
            "posts": posts,
        }

    summary = {
        "generated_at": None,  # set by write_export
        "week": week,
        "platforms": list(PLATFORMS),
        "weights": dict(MODULE_WEIGHTS),
        "creators": summary_creators,
    }
    return {"summary": summary, "creators": creator_pages}


def _platform_median(platform_block: Dict[str, Any], field: str) -> Optional[float]:
    values = [p[field] for p in platform_block.values() if p.get(field) is not None]
    if not values:
        return None
    values.sort()
    mid = len(values) // 2
    if len(values) % 2:
        return round(values[mid], 4)
    return round((values[mid - 1] + values[mid]) / 2, 4)


def scan_for_contacts(payload: Any, path: str = "$") -> List[str]:
    """Fails loudly if an email, phone number, or wa.me link slipped into the export."""
    violations: List[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            violations.extend(scan_for_contacts(value, f"{path}.{key}"))
    elif isinstance(payload, list):
        for idx, value in enumerate(payload):
            violations.extend(scan_for_contacts(value, f"{path}[{idx}]"))
    elif isinstance(payload, str):
        if _EMAIL.search(payload):
            violations.append(f"{path}: email-like string")
        if _WA_LINK.search(payload):
            violations.append(f"{path}: whatsapp reference")
        if _looks_like_phone(payload):
            violations.append(f"{path}: phone-like string")
    return violations


def write_export(export: Dict[str, Any], repo: DataRepo, generated_at: str) -> Path:
    """Validate, then write to a temp dir and move into place (section 7)."""
    export["summary"]["generated_at"] = generated_at

    violations = scan_for_contacts(export["summary"]) + scan_for_contacts(export["creators"])
    if violations:
        raise ValueError(f"public export would leak contact info: {violations}")

    dest_root = repo.root.parent / "export" / "public"
    avatars_src = dest_root / "avatars"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp) / "export"
        write_json(tmp_root / "summary.json", export["summary"])
        for slug, page in export["creators"].items():
            write_json(tmp_root / "creators" / f"{slug}.json", page)
        if avatars_src.is_dir():
            shutil.copytree(avatars_src, tmp_root / "avatars", dirs_exist_ok=True)

        if dest_root.exists():
            shutil.rmtree(dest_root)
        dest_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_root), str(dest_root))
    return dest_root
