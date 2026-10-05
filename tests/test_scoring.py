"""Section 8 scoring behaviour."""
from __future__ import annotations

import math

from social_media_ranking.models import RESOLUTION_ERROR, PlatformScore, RawMetrics
from social_media_ranking.scoring import (
    carry_forward,
    combine_scores,
    engagement_value,
    growth_value,
    min_max_normalize,
    reach_value,
    score_platform,
)


def make(name, platform="instagram", followers=1000, avg_likes=50.0, avg_comments=5.0, **kwargs):
    return RawMetrics(
        creator_name=name,
        platform=platform,
        handle=name.lower(),
        profile_url=f"https://{platform}.com/{name.lower()}",
        followers=followers,
        avg_likes=avg_likes,
        avg_comments=avg_comments,
        **kwargs,
    )


def test_min_max_normalize_spans_zero_to_hundred():
    assert min_max_normalize([1.0, 2.0, 3.0]) == [0.0, 50.0, 100.0]


def test_min_max_normalize_flat_field_is_neutral():
    assert min_max_normalize([5.0, 5.0]) == [50.0, 50.0]


def test_min_max_normalize_missing_values_score_zero():
    assert min_max_normalize([None, None]) == [0.0, 0.0]


def test_reach_is_log_scaled_so_one_giant_does_not_flatten_the_field():
    small, medium, giant = make("a", followers=1_000), make("b", followers=100_000), make("c", followers=10_000_000)
    scores = {s.creator_name: s.reach for s in score_platform([small, medium, giant])}
    # On a linear scale the 1k and 100k accounts would both round to ~0.
    assert scores["b"] > 40
    assert math.isclose(reach_value(giant), 7.0, abs_tol=0.001)


def test_engagement_prefers_provider_rate_when_present():
    record = make("a", provider_engagement_rate=0.077)
    assert engagement_value(record) == 0.077


def test_engagement_is_interactions_over_followers():
    record = make("a", followers=1000, avg_likes=80.0, avg_comments=20.0)
    assert math.isclose(engagement_value(record), 0.1)


def test_growth_needs_a_baseline():
    current = make("a", followers=1100)
    assert growth_value(current, None) is None
    assert math.isclose(growth_value(current, make("a", followers=1000)), 0.1)


def test_growth_is_zero_until_history_exists():
    scores = score_platform([make("a", followers=1000), make("b", followers=5000)])
    assert all(s.growth == 0.0 for s in scores)


def test_growth_counts_once_history_exists():
    previous = {
        "https://instagram.com/a": make("a", followers=1000),
        "https://instagram.com/b": make("b", followers=1000),
    }
    current = [make("a", followers=2000), make("b", followers=1010)]
    scores = {s.creator_name: s.growth for s in score_platform(current, previous)}
    assert scores["a"] == 100.0
    assert scores["b"] == 0.0


def test_platform_score_uses_the_50_35_15_split():
    previous = {"https://instagram.com/a": make("a", followers=500), "https://instagram.com/b": make("b", followers=500)}
    scores = {s.creator_name: s for s in score_platform([make("a", followers=1000), make("b", followers=1000)], previous)}
    top = scores["a"]
    expected = 0.50 * top.reach + 0.35 * top.engagement + 0.15 * top.growth
    assert math.isclose(top.score, round(expected, 2), abs_tol=0.01)


def test_failed_extractions_are_excluded_from_scoring():
    good = make("a")
    bad = make("b", resolution_status=RESOLUTION_ERROR)
    assert [s.creator_name for s in score_platform([good, bad])] == ["a"]


def test_weights_renormalize_across_resolved_platforms_only():
    # Creator with only YouTube should not be dragged down by absent platforms.
    scores = [
        PlatformScore(creator_name="solo", platform="youtube", handle="solo", score=80.0),
        PlatformScore(creator_name="broad", platform="youtube", handle="broad", score=80.0),
        PlatformScore(creator_name="broad", platform="threads", handle="broad", score=10.0),
    ]
    combined = {c.creator_name: c for c in combine_scores(scores)}
    assert combined["solo"].total_score == 80.0
    assert combined["broad"].total_score < 80.0
    assert combined["solo"].coverage == "1/7"
    assert combined["broad"].coverage == "2/7"


def test_creator_with_no_resolved_platform_is_excluded_not_zeroed():
    combined = combine_scores([PlatformScore(creator_name="ghost", platform="myspace", handle="ghost", score=90.0)])
    assert combined == []


def test_ranking_is_sorted_by_total_score():
    scores = [
        PlatformScore(creator_name="low", platform="youtube", handle="low", score=10.0),
        PlatformScore(creator_name="high", platform="youtube", handle="high", score=90.0),
    ]
    assert [c.creator_name for c in combine_scores(scores)] == ["high", "low"]


def test_carry_forward_keeps_last_known_good_score():
    current = [PlatformScore(creator_name="a", platform="youtube", handle="a", score=70.0)]
    previous = [
        PlatformScore(creator_name="a", platform="youtube", handle="a", score=60.0),
        PlatformScore(creator_name="b", platform="youtube", handle="b", score=55.0),
    ]
    carried = {s.creator_name: s for s in carry_forward(current, previous)}
    assert carried["a"].score == 70.0 and carried["a"].carried_over is False
    assert carried["b"].score == 55.0 and carried["b"].carried_over is True
