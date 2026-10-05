#!/usr/bin/env python3
"""Build one export from fixture data (brief deliverable #6).

Seeds a throwaway data repo with a few confirmed creators across several
weeks (so history/periods/carry-over all have something to show), runs the
real weekly-score + export-public code paths against it, then copies the
result into this repo at samples/data/latest/ so the site team can build
against the real shape before the first live run.

This writes only under samples/ in *this* repo -- it does not touch the
public site repo. Pushing samples/data/latest/ there is a separate, explicit
step (see README/the plan this was built from).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_media_ranking.models import CreatorProfile, Post, RawMetrics  # noqa: E402
from social_media_ranking.pipeline import load_profiles, stage_export_public, stage_weekly_score  # noqa: E402
from social_media_ranking.storage import DataRepo, write_csv, write_json  # noqa: E402
from social_media_ranking.links.extractor import write_profiles  # noqa: E402
from social_media_ranking.overrides import apply_creator_meta  # noqa: E402
from social_media_ranking.posts_cache import save_posts  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = REPO_ROOT / "samples" / "_build"
WEEKS = ["2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]

# (creator_name, platform, handle, {week: followers})
ACCOUNTS = [
    ("Lotus Sangha Live", "youtube", "@lotussangha", {w: f for w, f in zip(WEEKS, [590_000, 598_000, 602_000, 606_000, 610_000])}),
    ("Lotus Sangha Live", "instagram", "lotussangha", {w: f for w, f in zip(WEEKS, [700_000, 705_000, 708_000, 710_000, 710_000])}),
    ("Lotus Sangha Live", "facebook", "lotussangha", {w: f for w, f in zip(WEEKS, [None, None, None, None, None])}),
    ("Dharma Talks Weekly", "youtube", "@dharmatalks", {w: f for w, f in zip(WEEKS, [40_000, 41_000, 42_500, 44_000, 45_200])}),
    ("Dharma Talks Weekly", "x", "dharmatalks", {w: f for w, f in zip(WEEKS, [5_000, 5_050, 5_100, 5_150, 5_200])}),
    ("Mountain Monastery", "instagram", "mountainmonastery", {w: f for w, f in zip(WEEKS, [12_000, 12_100, None, None, 12_400])}),
]

OVERRIDES = {
    "Lotus Sangha Live": {
        "confirmation": "Confirmed",
        "slug": "lotus-sangha",
        "language": "en",
        "bio": "Daily dharma talks and guided meditation from the Lotus Sangha community.",
    },
    "Dharma Talks Weekly": {
        "confirmation": "Confirmed",
        "slug": "dharma-talks-weekly",
        "language": "en",
        "bio": "Weekly dharma talks on practical Buddhist practice.",
    },
    "Mountain Monastery": {
        "confirmation": "Pending",  # not confirmed -- should NOT appear in the export
        "slug": "mountain-monastery",
    },
}


def build_profiles() -> list[CreatorProfile]:
    seen = set()
    profiles = []
    for name, platform, handle, _ in ACCOUNTS:
        key = (name, platform)
        if key in seen:
            continue
        seen.add(key)
        profiles.append(
            CreatorProfile(
                creator_name=name,
                platform=platform,
                handle=handle,
                profile_url=f"https://{platform}.com/{handle}",
                status="resolved",
            )
        )
    return profiles


def seed_posts(repo: DataRepo) -> None:
    posts = [
        Post(
            platform="youtube",
            profile_url="https://youtube.com/@lotussangha",
            id=f"vid{i}",
            url=f"https://www.youtube.com/watch?v=vid{i}",
            published_at=f"2026-09-{20 + i:02d}T14:00:00Z",
            type="video",
            title=f"Guided meditation session {i}",
            thumbnail_url=f"https://i.ytimg.com/vi/vid{i}/hqdefault.jpg",
            duration_s=1800,
            views=62_900 - i * 1000,
            likes=5_200 - i * 100,
            comments=231 - i * 5,
            shares=None,
        )
        for i in range(1, 4)
    ]
    save_posts(repo, "youtube", "https://youtube.com/@lotussangha", posts)

    dharma_posts = [
        Post(
            platform="x",
            profile_url="https://x.com/dharmatalks",
            id=f"tw{i}",
            url=f"https://x.com/dharmatalks/status/{i}",
            published_at=f"2026-09-{22 + i:02d}T09:00:00Z",
            type="text",
            title="A short reflection on impermanence.",
            views=3_200,
            likes=180,
            comments=12,
            shares=9,
        )
        for i in range(1, 3)
    ]
    save_posts(repo, "x", "https://x.com/dharmatalks", dharma_posts)


def main() -> int:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    repo = DataRepo(WORK_DIR / "data")

    profiles = build_profiles()
    write_json(repo.overrides_json, OVERRIDES)
    apply_creator_meta(repo, profiles)
    write_profiles(repo, profiles)
    seed_posts(repo)

    for week in WEEKS:
        for name, platform, handle, by_week in ACCOUNTS:
            followers = by_week[week]
            record = RawMetrics(
                creator_name=name,
                platform=platform,
                handle=handle,
                profile_url=f"https://{platform}.com/{handle}",
                followers=followers,
                followers_source="youtube_api" if platform == "youtube" else "socialcrawl",
                posts_source="youtube_api" if platform == "youtube" else ("socialcrawl" if platform == "x" else ""),
                resolution_status="ok" if followers is not None else "error",
            )
            existing = []
            path = repo.raw_file(platform, week)
            rows = [r.to_row() for r in [record]]
            if path.is_file():
                import csv

                with path.open(newline="", encoding="utf-8") as handle_file:
                    existing = list(csv.DictReader(handle_file))
            merged = {r.get("profile_url"): r for r in existing}
            merged.update({r["profile_url"]: r for r in rows})
            write_csv(path, list(merged.values()), list(rows[0].keys()))

        all_profiles = load_profiles(repo.creator_profiles)
        scores = stage_weekly_score(repo, all_profiles, week, rebuild=True)
        stage_export_public(repo, all_profiles, scores, week)

    dest = REPO_ROOT / "samples" / "data" / "latest"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WORK_DIR / "export" / "public", dest)
    shutil.rmtree(WORK_DIR)

    summary = json.loads((dest / "summary.json").read_text())
    print(f"Wrote {dest} -- {len(summary['creators'])} confirmed creator(s): " f"{[c['slug'] for c in summary['creators']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
