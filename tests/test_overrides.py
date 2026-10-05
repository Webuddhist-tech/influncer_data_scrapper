"""Section 3 — slug assignment and overrides.json precedence."""
from __future__ import annotations

import json

from social_media_ranking.models import CONFIRMATION_DEFAULT, CreatorProfile
from social_media_ranking.overrides import apply_creator_meta, slugify
from social_media_ranking.storage import DataRepo


def profile(creator_name: str, platform: str, **kwargs) -> CreatorProfile:
    return CreatorProfile(
        creator_name=creator_name,
        platform=platform,
        handle=platform,
        profile_url=f"https://{platform}.com/{creator_name.lower()}",
        **kwargs,
    )


def test_slugify_lowercases_ascii_folds_and_hyphenates():
    assert slugify("Lotus Sangha Live") == "lotus-sangha-live"
    assert slugify("Ngawang Chöphel") == "ngawang-chophel"
    assert slugify("  Multiple   Spaces!! ") == "multiple-spaces"


def test_slug_is_derived_once_and_then_stays_stable(tmp_path):
    repo = DataRepo(tmp_path / "data")
    rows = [profile("Lotus Sangha", "instagram"), profile("Lotus Sangha", "youtube")]
    apply_creator_meta(repo, rows)
    assert rows[0].slug == rows[1].slug == "lotus-sangha"

    # Re-running with an already-assigned slug on one row keeps it, even if
    # the other row for the same creator has none yet.
    rows2 = [profile("Lotus Sangha", "instagram", slug="lotus-sangha"), profile("Lotus Sangha", "facebook")]
    apply_creator_meta(repo, rows2)
    assert rows2[1].slug == "lotus-sangha"


def test_duplicate_names_get_a_unique_suffixed_slug(tmp_path):
    repo = DataRepo(tmp_path / "data")
    # Two distinct creators whose names slugify to the same base.
    rows = [profile("Lotus Sangha", "instagram"), profile("Lotus, Sangha!!", "instagram")]
    apply_creator_meta(repo, rows)
    assert rows[0].slug == "lotus-sangha"
    assert rows[1].slug == "lotus-sangha-2"


def test_confirmation_defaults_to_pending_without_an_override(tmp_path):
    repo = DataRepo(tmp_path / "data")
    rows = [profile("New Creator", "instagram")]
    apply_creator_meta(repo, rows)
    assert rows[0].confirmation == CONFIRMATION_DEFAULT


def test_overrides_json_wins_over_the_sheet_value(tmp_path):
    repo = DataRepo(tmp_path / "data")
    repo.overrides_json.parent.mkdir(parents=True, exist_ok=True)
    repo.overrides_json.write_text(
        json.dumps({"Lotus Sangha": {"confirmation": "Confirmed", "slug": "lotus-sangha-live"}}),
        encoding="utf-8",
    )
    rows = [profile("Lotus Sangha", "instagram", confirmation="Pending")]
    apply_creator_meta(repo, rows)
    assert rows[0].confirmation == "Confirmed"
    assert rows[0].slug == "lotus-sangha-live"


def test_scraped_avatar_is_kept_but_override_wins_if_set(tmp_path):
    repo = DataRepo(tmp_path / "data")
    rows = [profile("Lotus Sangha", "instagram", avatar_url="https://ltcdn.linktr.ee/avatars/lotus.jpg")]
    apply_creator_meta(repo, rows)
    assert rows[0].avatar_url == "https://ltcdn.linktr.ee/avatars/lotus.jpg"

    repo.overrides_json.parent.mkdir(parents=True, exist_ok=True)
    repo.overrides_json.write_text(
        json.dumps({"Lotus Sangha": {"avatar_url": "https://example.com/better-photo.jpg"}}), encoding="utf-8"
    )
    rows2 = [profile("Lotus Sangha", "instagram", avatar_url="https://ltcdn.linktr.ee/avatars/lotus.jpg")]
    apply_creator_meta(repo, rows2)
    assert rows2[0].avatar_url == "https://example.com/better-photo.jpg"


def test_merged_into_override_is_applied_to_every_row(tmp_path):
    repo = DataRepo(tmp_path / "data")
    repo.overrides_json.parent.mkdir(parents=True, exist_ok=True)
    repo.overrides_json.write_text(
        json.dumps({"Dup Creator": {"merged_into": "lotus-sangha"}}),
        encoding="utf-8",
    )
    rows = [profile("Dup Creator", "instagram"), profile("Dup Creator", "youtube")]
    apply_creator_meta(repo, rows)
    assert all(r.merged_into == "lotus-sangha" for r in rows)
