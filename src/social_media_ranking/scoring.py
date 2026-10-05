"""Scoring metric v1 (section 8).

PlatformScore = 0.50*Reach + 0.35*Engagement + 0.15*Growth, each sub-score
min-max normalized to 0-100 against the other creators on that platform this
week. TotalScore renormalizes platform weights across only the platforms a
creator actually resolved.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence

from .config import ENGAGEMENT_WEIGHT, GROWTH_WEIGHT, PLATFORMS, PLATFORM_WEIGHTS, REACH_WEIGHT
from .models import RESOLUTION_OK, CreatorScore, PlatformScore, RawMetrics

# Returned when every creator has the same raw value, so min-max has no spread.
NEUTRAL_SCORE = 50.0


def min_max_normalize(values: Sequence[Optional[float]]) -> List[float]:
    """Scale values to 0-100. Missing values score 0; a flat field scores 50."""
    present = [v for v in values if v is not None]
    if not present:
        return [0.0 for _ in values]
    low, high = min(present), max(present)
    if math.isclose(high, low):
        return [NEUTRAL_SCORE if v is not None else 0.0 for v in values]
    span = high - low
    return [0.0 if v is None else (v - low) / span * 100.0 for v in values]


def reach_value(record: RawMetrics) -> Optional[float]:
    """log10(followers + 1) — keeps one 10M account from flattening everyone else."""
    if record.followers is None:
        return None
    return math.log10(max(record.followers, 0) + 1)


def engagement_value(record: RawMetrics) -> Optional[float]:
    """(avg likes + comments per post) / followers, or the provider's own rate."""
    if record.provider_engagement_rate is not None:
        return record.provider_engagement_rate
    if not record.followers:
        return None
    interactions = (record.avg_likes or 0.0) + (record.avg_comments or 0.0)
    if interactions == 0.0 and record.avg_views:
        # Threads/TikTok often expose views but not likes on the profile call.
        interactions = record.avg_views * 0.01
    if interactions == 0.0:
        return None
    return interactions / record.followers


def growth_value(record: RawMetrics, previous: Optional[RawMetrics]) -> Optional[float]:
    """Week-over-week follower growth rate, or None when there is no baseline."""
    if previous is None or not previous.followers or record.followers is None:
        return None
    return (record.followers - previous.followers) / previous.followers


def score_platform(
    records: Sequence[RawMetrics],
    previous: Optional[Dict[str, RawMetrics]] = None,
) -> List[PlatformScore]:
    """Score every creator on one platform, normalized against each other."""
    previous = previous or {}
    usable = [r for r in records if r.resolution_status == RESOLUTION_OK and r.followers is not None]
    if not usable:
        return []

    reach = min_max_normalize([reach_value(r) for r in usable])
    engagement = min_max_normalize([engagement_value(r) for r in usable])
    growth_raw = [growth_value(r, previous.get(r.profile_url)) for r in usable]
    # Section 8: growth is neutral-zero until at least two snapshots exist.
    growth = min_max_normalize(growth_raw) if any(g is not None for g in growth_raw) else [0.0] * len(usable)

    scored: List[PlatformScore] = []
    for idx, record in enumerate(usable):
        total = (
            REACH_WEIGHT * reach[idx]
            + ENGAGEMENT_WEIGHT * engagement[idx]
            + GROWTH_WEIGHT * growth[idx]
        )
        scored.append(
            PlatformScore(
                creator_name=record.creator_name,
                platform=record.platform,
                handle=record.handle,
                followers=record.followers,
                reach=round(reach[idx], 2),
                engagement=round(engagement[idx], 2),
                growth=round(growth[idx], 2),
                score=round(total, 2),
            )
        )
    return scored


def score_all(
    records_by_platform: Dict[str, Sequence[RawMetrics]],
    previous_by_platform: Optional[Dict[str, Dict[str, RawMetrics]]] = None,
) -> List[PlatformScore]:
    previous_by_platform = previous_by_platform or {}
    scored: List[PlatformScore] = []
    for platform, records in records_by_platform.items():
        scored.extend(score_platform(records, previous_by_platform.get(platform)))
    return scored


def combine_scores(
    platform_scores: Iterable[PlatformScore],
    weights: Optional[Dict[str, float]] = None,
) -> List[CreatorScore]:
    """Roll per-platform scores into a ranked total per creator.

    Weights are renormalized across only the platforms a creator resolved, so a
    creator with no LinkedIn is not penalized for a platform they never had.
    """
    weights = weights or PLATFORM_WEIGHTS
    by_creator: Dict[str, Dict[str, float]] = {}
    for entry in platform_scores:
        by_creator.setdefault(entry.creator_name, {})[entry.platform] = entry.score

    combined: List[CreatorScore] = []
    for creator_name, scores in by_creator.items():
        present = {p: s for p, s in scores.items() if p in weights}
        if not present:
            # Zero resolved platforms: excluded from ranking, never shown as "0".
            continue
        weight_sum = sum(weights[p] for p in present)
        total = sum(weights[p] * s for p, s in present.items()) / weight_sum
        combined.append(
            CreatorScore(
                creator_name=creator_name,
                total_score=round(total, 2),
                resolved_platforms=len(present),
                platform_scores={p: round(s, 2) for p, s in sorted(present.items())},
                coverage=f"{len(present)}/{len(PLATFORMS)}",
            )
        )
    combined.sort(key=lambda c: c.total_score, reverse=True)
    return combined


def carry_forward(
    current: Sequence[PlatformScore],
    previous: Sequence[PlatformScore],
) -> List[PlatformScore]:
    """Keep last week's score for anyone missing this week.

    A transient extraction error should not zero out a creator's ranking, so
    their last known good row is carried over and flagged.
    """
    seen = {(s.creator_name, s.platform) for s in current}
    carried = list(current)
    for entry in previous:
        if (entry.creator_name, entry.platform) not in seen:
            carried.append(
                PlatformScore(
                    creator_name=entry.creator_name,
                    platform=entry.platform,
                    handle=entry.handle,
                    followers=entry.followers,
                    reach=entry.reach,
                    engagement=entry.engagement,
                    growth=entry.growth,
                    score=entry.score,
                    carried_over=True,
                )
            )
    return carried
