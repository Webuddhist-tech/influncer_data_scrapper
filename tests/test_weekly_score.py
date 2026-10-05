"""Section 6/7 — weekly orchestration: carry-over cutoff and history rows."""
from __future__ import annotations

from social_media_ranking.models import CreatorProfile, RawMetrics
from social_media_ranking.storage import DataRepo, read_csv, write_csv
from social_media_ranking.weekly_score import score_week


def _profiles():
    return [CreatorProfile(creator_name="A", platform="instagram", handle="a", profile_url="https://instagram.com/a")]


def _write_week(repo: DataRepo, week: str, followers: int | None):
    record = RawMetrics(
        creator_name="A",
        platform="instagram",
        handle="a",
        profile_url="https://instagram.com/a",
        followers=followers,
        followers_source="socialcrawl",
        resolution_status="ok" if followers is not None else "error",
    )
    write_csv(repo.raw_file("instagram", week), [record.to_row()], list(record.to_row().keys()))


def test_history_row_marks_resolved_week_as_not_carried(tmp_path):
    repo = DataRepo(tmp_path / "data")
    _write_week(repo, "2026-09-07", 1000)
    score_week(repo, _profiles(), "2026-09-07")

    rows = read_csv(repo.history_file("2026-09-07"))
    assert rows[0]["resolved"] == "true"
    assert rows[0]["carried_over"] == "false"
    assert rows[0]["followers"] == "1000"
    assert rows[0]["followers_source"] == "socialcrawl"


def test_carry_over_cuts_off_after_two_consecutive_weeks(tmp_path):
    repo = DataRepo(tmp_path / "data")
    weeks = ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    # Resolved week 1, then unresolved (no raw row at all) for weeks 2-4.
    _write_week(repo, weeks[0], 1000)

    for week in weeks:
        score_week(repo, _profiles(), week)
        # Remove any raw snapshot for this week to simulate it staying unresolved
        # going forward (score_week reads what's on disk for `stamp` itself).

    history = {week: read_csv(repo.history_file(week))[0] for week in weeks}
    assert history[weeks[0]]["carried_over"] == "false"
    assert history[weeks[1]]["carried_over"] == "true"
    assert history[weeks[2]]["carried_over"] == "true"
    # Two consecutive carried weeks (2 and 3) is the cap; week 4 can't carry again.
    assert history[weeks[3]]["resolved"] == "false"
    assert history[weeks[3]]["carried_over"] == "false"


def test_no_resolved_or_carried_accounts_drops_the_creator_from_scores(tmp_path):
    repo = DataRepo(tmp_path / "data")
    scores = score_week(repo, _profiles(), "2026-09-07")
    assert scores == {}
    row = read_csv(repo.history_file("2026-09-07"))[0]
    assert row["resolved"] == "false"


def test_merged_into_creator_is_skipped_entirely(tmp_path):
    repo = DataRepo(tmp_path / "data")
    _write_week(repo, "2026-09-07", 1000)
    profiles = _profiles()
    profiles[0].merged_into = "other-slug"
    scores = score_week(repo, profiles, "2026-09-07")
    assert scores == {}
    assert read_csv(repo.history_file("2026-09-07")) == []
