"""Weekly orchestration for Phase 6 scoring (brief sections 6/7).

Turns this week's raw snapshots + cached posts into a module score per
creator, applies the carry-over rule, and writes
``data/history/{week}.csv`` — one row per creator x platform, the pipeline's
memory for growth and periods. Kept separate from pipeline.py's legacy
``score_platform`` flow so the two can be compared
(docs/sample-check-comparison.md).

Carry-forward reads the *previous week's history row*, not its raw
snapshot — a carried week still has no fresh raw file, so chaining through
raw snapshots alone breaks after the first carry. The history row always
holds the last effective followers value, whether it was resolved fresh or
itself carried, which is what both the carry rule and the growth module
need.
"""
from __future__ import annotations

import statistics
from typing import Dict, List, Optional, Sequence, Tuple

from .config import PLATFORMS
from .models import RESOLUTION_OK, CreatorProfile, RawMetrics
from .module_scoring import (
    AccountWeek,
    CreatorWeekScore,
    allow_carry_over,
    post_engagement_rate,
    score_creator_week,
)
from .posts_cache import load_posts
from .storage import DataRepo, read_csv, write_csv

HISTORY_FIELDS = (
    "week",
    "creator_name",
    "platform",
    "resolved",
    "carried_over",
    "followers",
    "followers_source",
    "posts_90d",
    "engagement_rate",
    "total_score",
)


def _load_raw_by_platform(repo: DataRepo, stamp: str) -> Dict[str, Dict[str, RawMetrics]]:
    """platform -> {profile_url: RawMetrics} for one week's raw snapshot."""
    out: Dict[str, Dict[str, RawMetrics]] = {}
    for platform in PLATFORMS:
        rows = read_csv(repo.raw_file(platform, stamp))
        out[platform] = {row["profile_url"]: RawMetrics.from_row(row) for row in rows if row.get("profile_url")}
    return out


def _previous_history_lookup(repo: DataRepo, previous_week: Optional[str]) -> Dict[Tuple[str, str], Dict[str, str]]:
    if not previous_week:
        return {}
    rows = read_csv(repo.history_file(previous_week))
    return {(r["creator_name"], r["platform"]): r for r in rows}


def _carry_over_streak(repo: DataRepo, creator_name: str, platform: str, before_week: str) -> int:
    """Consecutive weeks immediately before ``before_week`` this (creator, platform) was carried over."""
    weeks = [w for w in repo.history_weeks() if w < before_week]
    streak = 0
    for week in reversed(weeks):
        rows = read_csv(repo.history_file(week))
        match = next((r for r in rows if r["creator_name"] == creator_name and r["platform"] == platform), None)
        if match is None:
            break
        if str(match.get("carried_over", "")).lower() == "true":
            streak += 1
        else:
            break
    return streak


def creator_history_totals(repo: DataRepo, creator_name: str, upto_week: str) -> List[Tuple[str, float]]:
    """This creator's ``(week, total_score)`` for every week they had one, up
    to and including ``upto_week``, oldest first. A week the creator was
    entirely dropped (no resolved/carried accounts) has no entry here, so
    callers never need to re-align scores against a separate week list."""
    totals: List[Tuple[str, float]] = []
    for week in repo.history_weeks():
        if week > upto_week:
            continue
        rows = read_csv(repo.history_file(week))
        match = next((r for r in rows if r["creator_name"] == creator_name and r.get("total_score")), None)
        if match:
            totals.append((week, float(match["total_score"])))
    return totals


class _Entry:
    __slots__ = ("platform", "profile_url", "followers", "followers_source", "resolved_now", "carried_over")

    def __init__(self, platform: str, profile_url: str, followers: Optional[int], followers_source: str, resolved_now: bool, carried_over: bool):
        self.platform = platform
        self.profile_url = profile_url
        self.followers = followers
        self.followers_source = followers_source
        self.resolved_now = resolved_now
        self.carried_over = carried_over


def score_week(repo: DataRepo, all_profiles: Sequence[CreatorProfile], stamp: str) -> Dict[str, CreatorWeekScore]:
    """Compute this week's module score for every non-merged creator and
    write ``data/history/{week}.csv``. Returns ``{creator_name: CreatorWeekScore}``."""
    raw_by_platform = _load_raw_by_platform(repo, stamp)
    previous_week = max((w for w in repo.history_weeks() if w < stamp), default=None)
    previous_history = _previous_history_lookup(repo, previous_week)

    by_creator: Dict[str, List[CreatorProfile]] = {}
    for profile in all_profiles:
        if profile.merged_into:
            continue
        by_creator.setdefault(profile.creator_name, []).append(profile)

    scores: Dict[str, CreatorWeekScore] = {}
    history_rows: List[Dict[str, object]] = []

    for creator_name, profile_rows in by_creator.items():
        entries: List[_Entry] = []
        previous_total_followers = 0
        had_previous = False

        for profile_row in profile_rows:
            platform = profile_row.platform
            current = raw_by_platform.get(platform, {}).get(profile_row.profile_url)
            resolved_now = current is not None and current.resolution_status == RESOLUTION_OK

            prev_row = previous_history.get((creator_name, platform))
            prev_followers_raw = prev_row.get("followers") if prev_row else ""
            prev_followers = int(prev_followers_raw) if prev_followers_raw not in (None, "") else None
            if prev_followers is not None:
                had_previous = True
                previous_total_followers += prev_followers

            if resolved_now:
                entries.append(_Entry(platform, profile_row.profile_url, current.followers, current.followers_source, True, False))
                continue

            streak = _carry_over_streak(repo, creator_name, platform, stamp)
            if prev_followers is not None and allow_carry_over(streak):
                entries.append(
                    _Entry(platform, profile_row.profile_url, prev_followers, prev_row.get("followers_source", ""), False, True)
                )
            else:
                entries.append(_Entry(platform, profile_row.profile_url, None, "", False, False))

        usable = [e for e in entries if e.followers is not None]
        if not usable:
            for entry in entries:
                history_rows.append(_history_row(stamp, creator_name, entry, posts_90d="", engagement_rate="", total_score=""))
            continue

        accounts: Dict[str, AccountWeek] = {}
        for entry in usable:
            posts = load_posts(repo, entry.platform, entry.profile_url)
            accounts[entry.platform] = AccountWeek(
                platform=entry.platform,
                followers=entry.followers,
                posts=posts,
                carried_over=entry.carried_over,
                has_post_data=bool(posts),
            )

        previous_total = previous_total_followers if had_previous else None
        result = score_creator_week(list(accounts.values()), previous_total)
        scores[creator_name] = result

        for entry in entries:
            account = accounts.get(entry.platform)
            if account is None:
                history_rows.append(_history_row(stamp, creator_name, entry, posts_90d="", engagement_rate="", total_score=""))
                continue
            rates = [r for r in (post_engagement_rate(p, account.followers) for p in account.posts) if r is not None]
            engagement_rate = round(statistics.median(rates), 4) if rates else ""
            history_rows.append(
                _history_row(
                    stamp,
                    creator_name,
                    entry,
                    posts_90d=len(account.posts),
                    engagement_rate=engagement_rate,
                    total_score=result.total_score if result.total_score is not None else "",
                )
            )

    write_csv(repo.history_file(stamp), history_rows, list(HISTORY_FIELDS))
    return scores


def _history_row(stamp: str, creator_name: str, entry: _Entry, posts_90d, engagement_rate, total_score) -> Dict[str, object]:
    return {
        "week": stamp,
        "creator_name": creator_name,
        "platform": entry.platform,
        "resolved": "true" if entry.resolved_now else "false",
        "carried_over": "true" if entry.carried_over else "false",
        "followers": entry.followers if entry.followers is not None else "",
        "followers_source": entry.followers_source,
        "posts_90d": posts_90d,
        "engagement_rate": engagement_rate,
        "total_score": total_score,
    }
