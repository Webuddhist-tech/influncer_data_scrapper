"""Section 7 — public export: confirmed-only, allowlist, no-contacts, atomic write."""
from __future__ import annotations

import json

import pytest

from social_media_ranking.export_public import build_export, scan_for_contacts, write_export
from social_media_ranking.models import CreatorProfile, RawMetrics
from social_media_ranking.module_scoring import AccountWeek, score_creator_week
from social_media_ranking.storage import DataRepo, write_csv
from social_media_ranking.weekly_score import score_week


def _confirmed_profile(**kwargs) -> CreatorProfile:
    defaults = dict(
        creator_name="Lotus Sangha",
        platform="instagram",
        handle="lotussangha",
        profile_url="https://instagram.com/lotussangha",
        status="resolved",
        confirmation="Confirmed",
        slug="lotus-sangha",
    )
    defaults.update(kwargs)
    return CreatorProfile(**defaults)


def _seed_week(repo: DataRepo, week: str, followers: int = 50_000):
    record = RawMetrics(
        creator_name="Lotus Sangha",
        platform="instagram",
        handle="lotussangha",
        profile_url="https://instagram.com/lotussangha",
        followers=followers,
        followers_source="socialcrawl",
        resolution_status="ok",
    )
    write_csv(repo.raw_file("instagram", week), [record.to_row()], list(record.to_row().keys()))


def test_unconfirmed_creators_are_excluded(tmp_path):
    repo = DataRepo(tmp_path / "data")
    _seed_week(repo, "2026-09-07")
    profiles = [_confirmed_profile(confirmation="Pending")]
    scores = score_week(repo, profiles, "2026-09-07")
    export = build_export(repo, profiles, scores, week="2026-09-07", download_avatars=False)
    assert export["summary"]["creators"] == []


def test_confirmed_creator_with_resolved_account_is_exported(tmp_path):
    repo = DataRepo(tmp_path / "data")
    _seed_week(repo, "2026-09-07")
    profiles = [_confirmed_profile()]
    scores = score_week(repo, profiles, "2026-09-07")
    export = build_export(repo, profiles, scores, week="2026-09-07", download_avatars=False)

    assert len(export["summary"]["creators"]) == 1
    entry = export["summary"]["creators"][0]
    assert entry["slug"] == "lotus-sangha"
    assert entry["followers"] == 50_000
    assert "instagram" in entry["platforms"]

    page = export["creators"]["lotus-sangha"]
    assert page["platforms"]["instagram"]["followers"] == 50_000
    assert page["platforms"]["instagram"]["carried_over"] is False
    assert "modules" in page and page["modules"]["reach"] > 0


def test_email_and_whatsapp_never_appear_in_the_export_even_when_set(tmp_path):
    repo = DataRepo(tmp_path / "data")
    _seed_week(repo, "2026-09-07")
    profiles = [_confirmed_profile(email="organizer-contact@example.com", whatsapp="+15551234567")]
    scores = score_week(repo, profiles, "2026-09-07")
    export = build_export(repo, profiles, scores, week="2026-09-07", download_avatars=False)
    assert scan_for_contacts(export["summary"]) == []
    assert scan_for_contacts(export["creators"]) == []


def test_duplicate_platform_rows_do_not_hide_the_resolved_one(tmp_path):
    # Real bug found running against the live roster: a creator with two
    # profile rows for the same platform (e.g. two YouTube links from one
    # Linktree page), one resolved and one not, could lose the resolved
    # one's data in the export because {row["platform"]: row for row in
    # rows} let the unresolved duplicate overwrite it.
    repo = DataRepo(tmp_path / "data")
    _seed_week(repo, "2026-09-07")  # instagram, resolved, 50_000 followers
    good_youtube = CreatorProfile(
        creator_name="Lotus Sangha",
        platform="youtube",
        handle="lotussangha",
        profile_url="https://youtube.com/@lotussangha",
        status="resolved",
    )
    duplicate_youtube = CreatorProfile(
        creator_name="Lotus Sangha",
        platform="youtube",
        handle="lotussanghaold",
        profile_url="https://youtube.com/@lotussanghaold",  # never resolves -- no raw row for this URL
        status="resolved",
    )
    write_csv(
        repo.raw_file("youtube", "2026-09-07"),
        [RawMetrics(creator_name="Lotus Sangha", platform="youtube", handle="lotussangha",
                     profile_url="https://youtube.com/@lotussangha", followers=12_000,
                     followers_source="youtube_api", resolution_status="ok").to_row()],
        None,
    )
    # Put the never-resolved duplicate *last* so it would win a naive dict comprehension.
    profiles = [_confirmed_profile(), good_youtube, duplicate_youtube]
    scores = score_week(repo, profiles, "2026-09-07")
    export = build_export(repo, profiles, scores, week="2026-09-07", download_avatars=False)

    entry = export["summary"]["creators"][0]
    assert "youtube" in entry["platforms"]
    page = export["creators"]["lotus-sangha"]
    assert page["platforms"]["youtube"]["followers"] == 12_000


def test_creator_with_no_slug_is_skipped(tmp_path, caplog):
    repo = DataRepo(tmp_path / "data")
    _seed_week(repo, "2026-09-07")
    profiles = [_confirmed_profile(slug="")]
    scores = score_week(repo, profiles, "2026-09-07")
    export = build_export(repo, profiles, scores, week="2026-09-07", download_avatars=False)
    assert export["summary"]["creators"] == []


def test_scan_for_contacts_flags_email_and_whatsapp():
    assert scan_for_contacts({"bio": "reach me at hi@example.com"})
    assert scan_for_contacts({"bio": "message us on wa.me/1234567890"})
    assert scan_for_contacts({"bio": "just a normal bio with no contact info"}) == []


def test_scan_for_contacts_flags_phone_numbers_but_not_dates():
    assert scan_for_contacts({"bio": "call +1 555-123-4567 anytime"})
    assert scan_for_contacts({"week": "2026-09-07", "generated_at": "2026-09-07T02:00:00Z"}) == []


def test_write_export_refuses_to_write_when_contacts_leak(tmp_path):
    repo = DataRepo(tmp_path / "data")
    export = {"summary": {"creators": [{"bio": "hi@example.com"}]}, "creators": {}}
    with pytest.raises(ValueError):
        write_export(export, repo, generated_at="2026-09-07T00:00:00Z")
    assert not (repo.root.parent / "export" / "public").exists()


def test_write_export_is_atomic_and_round_trips_json(tmp_path):
    repo = DataRepo(tmp_path / "data")
    _seed_week(repo, "2026-09-07")
    profiles = [_confirmed_profile()]
    scores = score_week(repo, profiles, "2026-09-07")
    export = build_export(repo, profiles, scores, week="2026-09-07", download_avatars=False)

    dest = write_export(export, repo, generated_at="2026-09-07T02:00:00Z")
    summary = json.loads((dest / "summary.json").read_text())
    assert summary["generated_at"] == "2026-09-07T02:00:00Z"
    assert summary["week"] == "2026-09-07"
    page = json.loads((dest / "creators" / "lotus-sangha.json").read_text())
    assert page["slug"] == "lotus-sangha"
