"""Section 6 — module scales, re-weighting, confidence, carry-over, periods."""
from __future__ import annotations

import math

from social_media_ranking.models import Post
from social_media_ranking.module_scoring import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    AccountWeek,
    activity_module,
    allow_carry_over,
    breadth_module,
    combine_modules,
    compute_confidence,
    compute_periods,
    engagement_module,
    growth_module,
    post_engagement_rate,
    reach_module,
    score_creator_week,
)


def post(**kwargs) -> Post:
    defaults = dict(platform="youtube", profile_url="u", id="1", url="", published_at="2026-09-01T00:00:00+00:00")
    defaults.update(kwargs)
    return Post(**defaults)


def test_reach_module_hits_100_at_one_million_followers():
    assert math.isclose(reach_module(1_000_000), 100.0, abs_tol=0.01)
    assert reach_module(0) is None
    assert reach_module(None) is None


def test_reach_module_is_half_at_the_sqrt_of_one_million():
    # log10(1000) / 6 * 100 = 50
    assert math.isclose(reach_module(1_000), 50.0, abs_tol=0.01)


def test_post_engagement_rate_prefers_views_falls_back_to_followers():
    with_views = post(likes=60, comments=0, shares=0, views=1000)
    assert math.isclose(post_engagement_rate(with_views, followers=5000), 0.06)
    no_views = post(likes=60, comments=0, shares=0, views=None)
    assert math.isclose(post_engagement_rate(no_views, followers=1000), 0.06)


def test_post_engagement_rate_is_none_when_every_part_is_null():
    assert post_engagement_rate(post(likes=None, comments=None, shares=None, views=1000), followers=100) is None


def test_engagement_module_uses_the_median_across_every_platform():
    accounts = [
        AccountWeek(platform="youtube", followers=1000, posts=[post(likes=60, comments=0, shares=0, views=1000)]),
        AccountWeek(platform="instagram", followers=1000, posts=[post(id="2", likes=120, comments=0, shares=0, views=1000)]),
    ]
    # rates: 0.06, 0.12 -> median 0.09 -> 0.09/0.06*100 = 150, clamped to 100
    assert engagement_module(accounts) == 100.0


def test_engagement_module_is_missing_without_any_usable_post():
    assert engagement_module([AccountWeek(platform="youtube", followers=1000, posts=[])]) is None


def test_activity_module_caps_at_three_posts_per_week():
    posts = [post(id=str(i)) for i in range(39)]  # ~3/week over 90 days
    accounts = [AccountWeek(platform="youtube", posts=posts, has_post_data=True)]
    assert 95 <= activity_module(accounts) <= 100


def test_activity_module_is_missing_when_posts_were_never_fetched():
    accounts = [AccountWeek(platform="youtube", posts=[], has_post_data=False)]
    assert activity_module(accounts) is None


def test_activity_module_is_zero_not_missing_when_fetched_and_empty():
    accounts = [AccountWeek(platform="youtube", posts=[], has_post_data=True)]
    assert activity_module(accounts) == 0.0


def test_growth_module_flat_week_is_fifty_and_two_percent_is_a_hundred():
    assert growth_module(1000, 1000) == 50.0
    assert math.isclose(growth_module(1020, 1000), 100.0, abs_tol=0.01)
    assert growth_module(1000, None) is None


def test_growth_module_clamps_at_zero_for_a_big_drop():
    assert growth_module(500, 1000) == 0.0


def test_breadth_module_endpoints_match_the_brief():
    assert breadth_module(1) == 12.0 / 25.0 * 100.0
    assert breadth_module(4) == 100.0
    assert breadth_module(7) == 100.0
    assert breadth_module(0) is None


def test_combine_modules_reweights_proportionally_when_one_is_missing():
    modules = {"reach": 80.0, "engagement": None, "activity": 60.0, "growth": 50.0, "breadth": 100.0}
    total, missing = combine_modules(modules)
    assert missing == ["engagement"]
    # weight_sum excludes engagement's 30 -> 70; expected weighted average over the rest.
    expected = (35 * 80.0 + 20 * 60.0 + 10 * 50.0 + 5 * 100.0) / (35 + 20 + 10 + 5)
    assert math.isclose(total, round(expected, 1))


def test_combine_modules_refuses_to_score_from_breadth_alone():
    # Real bug caught via scripts/sample_check_comparison.py: 4 "resolved"
    # platforms with zero known followers anywhere and no posts must not
    # reweight into a perfect breadth-only 100.
    modules = {"reach": None, "engagement": None, "activity": None, "growth": None, "breadth": 100.0}
    total, missing = combine_modules(modules)
    assert total is None
    assert missing == ["activity", "engagement", "growth", "reach"]


def test_combine_modules_all_missing_returns_none():
    total, missing = combine_modules({k: None for k in ("reach", "engagement", "activity", "growth", "breadth")})
    assert total is None
    assert len(missing) == 5


def test_confidence_is_high_only_when_everything_is_clean():
    assert compute_confidence([], 0, 5, 5) == CONFIDENCE_HIGH


def test_confidence_drops_one_step_for_a_single_problem():
    assert compute_confidence(["growth"], 0, 5, 5) == CONFIDENCE_MEDIUM
    assert compute_confidence([], 1, 5, 5) == CONFIDENCE_MEDIUM
    assert compute_confidence([], 0, 4, 5) == CONFIDENCE_MEDIUM


def test_confidence_drops_to_low_for_a_severe_problem():
    assert compute_confidence([], 0, 2, 5) == CONFIDENCE_LOW  # 40% coverage
    assert compute_confidence(["engagement", "growth"], 0, 5, 5) == CONFIDENCE_LOW


def test_carry_over_cutoff_is_two_consecutive_weeks():
    assert allow_carry_over(0) is True
    assert allow_carry_over(1) is True
    assert allow_carry_over(2) is False
    assert allow_carry_over(3) is False


def test_compute_periods_is_null_before_there_is_history():
    periods = compute_periods([84.1])
    assert periods["week"] == [84.1, None]
    assert periods["month"] == [84.1, None]
    assert periods["all"] == [84.1, None]


def test_compute_periods_week_month_all_with_enough_history():
    totals = [70.0, 72.0, 74.0, 76.0, 78.0, 80.0, 82.0, 84.0, 86.0]
    periods = compute_periods(totals)
    assert periods["week"] == [86.0, 84.0]
    assert periods["month"][0] == round(sum(totals[-4:]) / 4, 1)
    assert periods["month"][1] == round(sum(totals[-8:-4]) / 4, 1)
    assert periods["all"][0] == round(sum(totals) / len(totals), 1)
    assert periods["all"][1] == round(sum(totals[:-1]) / (len(totals) - 1), 1)


def test_score_creator_week_end_to_end_prefers_reach_over_a_single_tidy_handle():
    # A Gangkar-Metok-shaped creator: big reach, no post/engagement data yet.
    big_reach = [AccountWeek(platform="youtube", followers=14_700, posts=[], has_post_data=False)]
    small_tidy = [
        AccountWeek(
            platform="instagram",
            followers=2_000,
            posts=[post(likes=200, comments=10, shares=0, views=2000)],
            has_post_data=True,
        )
    ]
    big = score_creator_week(big_reach, previous_total_followers=None)
    small = score_creator_week(small_tidy, previous_total_followers=None)
    assert big.modules["reach"] > small.modules["reach"]
