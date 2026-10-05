"""Creator-level metadata: manual fixes (section 2) and slug assignment (section 3).

``data/creator_profiles.csv`` is generated and re-written every run, so it is
never hand-edited directly. Manual fixes — confirmation status, a forced
slug, a merge decision, a language tag — go in ``data/overrides.json``
instead, keyed by ``creator_name``, and are layered on top here. A slug that
isn't overridden is derived once and then persisted back onto the CSV row so
it never changes between runs, the same way ``platform_id`` already caches a
resolved YouTube channel ID.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Sequence

from .models import CONFIRMATION_DEFAULT, CreatorProfile
from .storage import DataRepo

OVERRIDE_FIELDS = ("slug", "confirmation", "merged_into", "language", "bio", "avatar_url")


def load_overrides(repo: DataRepo) -> Dict[str, Dict[str, str]]:
    path = repo.overrides_json
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    return {str(name): dict(fields) for name, fields in raw.items()}


def slugify(name: str) -> str:
    """URL-safe, ASCII, hyphenated — e.g. "Lotus Sangha Live" -> "lotus-sangha-live"."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "creator"


def _unique_slug(base: str, taken: Dict[str, str], creator_name: str) -> str:
    """``taken`` maps slug -> the creator_name it already belongs to this run."""
    if taken.get(base, creator_name) == creator_name:
        return base
    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if taken.get(candidate, creator_name) == creator_name:
            return candidate
        suffix += 1


def apply_creator_meta(repo: DataRepo, profiles: Sequence[CreatorProfile]) -> List[CreatorProfile]:
    """Layer overrides.json onto every row, then fill in any missing slug.

    Mutates and returns ``profiles`` in place (same pattern as
    ``YouTubeExtractor.resolve_channel_ids`` caching ``platform_id``), so the
    caller's subsequent ``write_profiles`` persists the result.
    """
    overrides = load_overrides(repo)
    by_creator: Dict[str, List[CreatorProfile]] = {}
    for profile in profiles:
        by_creator.setdefault(profile.creator_name, []).append(profile)

    taken: Dict[str, str] = {}
    for creator_name, rows in by_creator.items():
        existing_slug = next((r.slug for r in rows if r.slug), "")
        if existing_slug:
            taken[existing_slug] = creator_name

    for creator_name, rows in by_creator.items():
        override = overrides.get(creator_name, {})

        confirmation = override.get("confirmation") or next(
            (r.confirmation for r in rows if r.confirmation), CONFIRMATION_DEFAULT
        )
        merged_into = override.get("merged_into") or next((r.merged_into for r in rows if r.merged_into), "")
        language = override.get("language") or next((r.language for r in rows if r.language), "")
        # override wins over whatever Linktree's own page happened to show.
        avatar_url = override.get("avatar_url") or next((r.avatar_url for r in rows if r.avatar_url), "")
        # Sheet-only, no override -- matches Desktop/ranking's OVERRIDE_FIELDS,
        # which never included contact info either.
        email = next((r.email for r in rows if r.email), "")
        whatsapp = next((r.whatsapp for r in rows if r.whatsapp), "")

        slug = override.get("slug") or next((r.slug for r in rows if r.slug), "")
        if not slug:
            slug = _unique_slug(slugify(creator_name), taken, creator_name)
        taken[slug] = creator_name

        for row in rows:
            row.confirmation = confirmation
            row.merged_into = merged_into
            row.language = language
            row.slug = slug
            row.avatar_url = avatar_url
            row.email = email
            row.whatsapp = whatsapp

    return list(profiles)
