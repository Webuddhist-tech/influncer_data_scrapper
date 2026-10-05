"""Phase 6 module scoring (brief section 6) — kept separate from scoring.py's
legacy ``score_platform``/``combine_scores``, which stay untouched for the
old-vs-new sample-check comparison (docs/sample-check-comparison.md).

Why a separate module: the legacy score mixes data-quality signals
(completeness/consistency) into the ranking itself, which is why a creator
like "Gangkar Metok" — real reach, untidy handles — loses to a tidier but
smaller profile (data/sample-check.md, read from the sibling project this
brief was originally written against). This module moves those signals into
``confidence`` instead and scores only real influence: reach, engagement,
activity, growth, breadth.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import POST_WINDOW_DAYS
from .models import Post

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# Section 6 default weights — shown in the README alongside this table. Kept
# as ints so the public export's summary.json prints "35" not "35.0".
MODULE_WEIGHTS: Dict[str, float] = {
    "reach": 35,
    "engagement": 30,
    "activity": 20,
    "growth": 10,
    "breadth": 5,
}

# Breadth points (section 6: "1 platform = 12 ... 4+ = 25"). These are the
# exact values from Desktop/ranking's scoring.py (the sibling project this
# brief was written against), not a guess.
BREADTH_POINTS: Dict[int, float] = {1: 12.0, 2: 18.0, 3: 22.0}
BREADTH_MAX_POINTS = 25.0

# Carry-over cap (section 6): an account can be carried for at most this many
# consecutive weeks before it's treated as truly unresolved.
MAX_CARRY_OVER_WEEKS = 2


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class AccountWeek:
    """One platform account's inputs for this creator this week."""

    platform: str
    followers: Optional[int] = None
    posts: Sequence[Post] = field(default_factory=list)
    carried_over: bool = False
    # True once a posts fetch was actually attempted for this account, so a
    # genuine zero posts doesn't get confused with "we never checked".
    has_post_data: bool = False


def reach_module(total_followers: Optional[int]) -> Optional[float]:
    """``min(100, 100 * log10(total followers) / 6)`` — 1M followers = 100."""
    if not total_followers or total_followers <= 0:
        return None
    return min(100.0, 100.0 * math.log10(total_followers) / 6.0)


def post_engagement_rate(post: Post, followers: Optional[int]) -> Optional[float]:
    """(likes + comments + shares) / views, or / followers when views aren't public.

    Skips null parts; returns None (not 0) when every part is null.
    """
    parts = (post.likes, post.comments, post.shares)
    if all(part is None for part in parts):
        return None
    numerator = sum(part or 0 for part in parts)
    denominator = post.views if post.views is not None else followers
    if not denominator:
        return None
    return numerator / denominator


def engagement_module(accounts: Sequence[AccountWeek]) -> Optional[float]:
    """``min(100, median engagement rate / 0.06 * 100)`` across every post, every platform."""
    rates: List[float] = []
    for account in accounts:
        for post in account.posts:
            rate = post_engagement_rate(post, account.followers)
            if rate is not None:
                rates.append(rate)
    if not rates:
        return None
    return min(100.0, statistics.median(rates) / 0.06 * 100.0)


def activity_module(accounts: Sequence[AccountWeek]) -> Optional[float]:
    """``min(100, posts_per_week / 3 * 100)`` over the 90-day window.

    Missing (not zero) when no account actually had a posts fetch attempted —
    a creator who really posted nothing still scores 0, which is different
    from "we don't know".
    """
    if not any(account.has_post_data for account in accounts):
        return None
    total_posts = sum(len(account.posts) for account in accounts)
    posts_per_week = total_posts / (POST_WINDOW_DAYS / 7.0)
    return min(100.0, posts_per_week / 3.0 * 100.0)


def growth_module(total_followers: Optional[int], previous_total_followers: Optional[int]) -> Optional[float]:
    """``clamp(50 + growth/0.02*50, 0, 100)``; flat week = 50, +2%/week = 100."""
    if not total_followers or not previous_total_followers:
        return None
    growth = (total_followers - previous_total_followers) / previous_total_followers
    return _clamp(50.0 + growth / 0.02 * 50.0, 0.0, 100.0)


def breadth_module(platform_count: int) -> Optional[float]:
    if platform_count <= 0:
        return None
    points = BREADTH_POINTS.get(platform_count, BREADTH_MAX_POINTS)
    return points / BREADTH_MAX_POINTS * 100.0


def combine_modules(
    modules: Dict[str, Optional[float]], weights: Dict[str, float] = MODULE_WEIGHTS
) -> Tuple[Optional[float], List[str]]:
    """Weighted sum, re-weighted across only the present modules (section 6).

    Returns ``(total_score, missing_modules)``; ``total_score`` is ``None``
    only when every single module is missing (nothing to score at all).
    """
    present = {name: value for name, value in modules.items() if value is not None}
    missing = sorted(name for name in weights if name not in present)
    if not present:
        return None, missing
    if "reach" not in present and "engagement" not in present:
        # Neither headline signal is available -- reweighting activity/growth/
        # breadth alone would produce a number that looks precise but
        # measures nothing (e.g. breadth=100 from platform count with zero
        # known followers anywhere). Section 2: never invent a number.
        return None, missing
    weight_sum = sum(weights[name] for name in present)
    total = sum(weights[name] * present[name] for name in present) / weight_sum
    return round(total, 1), missing


def compute_confidence(missing_modules: Sequence[str], carried_over_count: int, resolved_count: int, total_count: int) -> str:
    """high -> medium -> low (README has the full rule, this is it verbatim):

    - **low** if anything is severe: 2+ modules missing, 2+ accounts carried
      over, or at most half of this creator's platforms resolved this week.
    - **medium** if anything is merely off: any module missing, any account
      carried over, or coverage below 100% but above the severe line.
    - **high** otherwise — full coverage, every module present, nothing
      carried over.
    """
    coverage = resolved_count / total_count if total_count else 0.0
    severe = len(missing_modules) >= 2 or carried_over_count >= 2 or coverage <= 0.5
    mild = bool(missing_modules) or carried_over_count >= 1 or coverage < 1.0
    if severe:
        return CONFIDENCE_LOW
    if mild:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_HIGH


def allow_carry_over(previous_streak: int, max_weeks: int = MAX_CARRY_OVER_WEEKS) -> bool:
    """An unresolved account may carry forward only if it hasn't already for ``max_weeks`` running."""
    return previous_streak < max_weeks


def compute_periods(history_totals: Sequence[float]) -> Dict[str, List[Optional[float]]]:
    """``history_totals``: oldest first, ending with this week's total_score.

    Each period is ``[current, previous]``; ``previous`` is ``None`` where
    there isn't enough history yet (section 7).
    """
    n = len(history_totals)
    if n == 0:
        return {"week": [None, None], "month": [None, None], "all": [None, None]}

    week = [round(history_totals[-1], 1), round(history_totals[-2], 1) if n >= 2 else None]

    last4 = history_totals[-4:]
    prev4 = history_totals[-8:-4]
    month = [round(statistics.mean(last4), 1), round(statistics.mean(prev4), 1) if prev4 else None]

    all_period = [
        round(statistics.mean(history_totals), 1),
        round(statistics.mean(history_totals[:-1]), 1) if n >= 2 else None,
    ]

    return {"week": week, "month": month, "all": all_period}


@dataclass
class CreatorWeekScore:
    total_score: Optional[float]
    modules: Dict[str, float]
    missing_modules: List[str]
    confidence: str
    total_followers: Optional[int]
    follower_growth: Optional[float]


def score_creator_week(
    accounts: Sequence[AccountWeek],
    previous_total_followers: Optional[int],
) -> CreatorWeekScore:
    """One creator's full module score for one week."""
    total_followers = sum(a.followers for a in accounts if a.followers) or None
    follower_growth = None
    if total_followers and previous_total_followers:
        follower_growth = (total_followers - previous_total_followers) / previous_total_followers

    modules = {
        "reach": reach_module(total_followers),
        "engagement": engagement_module(accounts),
        "activity": activity_module(accounts),
        "growth": growth_module(total_followers, previous_total_followers),
        "breadth": breadth_module(len(accounts)),
    }
    total_score, missing = combine_modules(modules)
    present_modules = {name: round(value, 1) for name, value in modules.items() if value is not None}

    resolved = sum(1 for a in accounts if a.followers is not None and not a.carried_over)
    carried = sum(1 for a in accounts if a.carried_over)
    confidence = compute_confidence(missing, carried, resolved, len(accounts))

    return CreatorWeekScore(
        total_score=total_score,
        modules=present_modules,
        missing_modules=missing,
        confidence=confidence,
        total_followers=total_followers,
        follower_growth=follower_growth,
    )
