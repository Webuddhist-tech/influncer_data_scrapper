#!/usr/bin/env python3
"""Regenerate docs/sample-check-comparison.md (brief deliverable #5).

Builds a synthetic 10-creator fixture from the real numbers documented in
Desktop/ranking's data/sample-check.md (follower counts, platform spread,
bio/handle quality) -- this repo has no real creator roster of its own, so a
literal re-run isn't possible; this is the closest faithful substitute (see
the plan's Context section). Scores each creator with both the legacy
score_platform/combine_scores and the new module_scoring, then writes the
comparison doc.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_media_ranking.models import RawMetrics  # noqa: E402
from social_media_ranking.module_scoring import AccountWeek, score_creator_week  # noqa: E402
from social_media_ranking.scoring import combine_scores, score_all  # noqa: E402

# name, {platform: followers_or_None}, has_bio, handles_match
CREATORS = [
    ("Gangkar Metok", {"youtube": 14_700, "instagram": None, "facebook": None, "tiktok": None}, True, True),
    ("SANTOSH SAKHARAM AMBHORE", {"youtube": 9_580, "instagram": None, "facebook": None, "tiktok": None}, True, True),
    ("Arun Sonune", {"youtube": 8_790, "instagram": None, "facebook": None, "tiktok": None}, False, False),
    ("Lopon Jurmay Karma", {"youtube": 8_150, "instagram": None, "facebook": None, "tiktok": None}, False, True),
    ("Paudel Dhruba", {"youtube": 4_360, "instagram": None, "facebook": None}, False, True),
    (
        "Uttam Shetty (Nagaloka Center)",
        {"youtube": 3_620, "instagram": None, "facebook": None, "tiktok": None, "threads": None, "linkedin": None, "x": None},
        False,
        True,
    ),
    ("Kunsang Tenzing", {"youtube": 2_050, "instagram": None, "facebook": None, "threads": None, "x": None}, True, True),
    ("Sonam Choden", {"tiktok": None, "instagram": None, "facebook": None, "threads": None}, True, False),
    ("Jay Liao, Yeshe gyatso", {"instagram": None}, True, False),
    ("Chhewang Dorjee", {}, False, False),
]


def legacy_score(name, platforms):
    records_by_platform = {}
    for platform, followers in platforms.items():
        records_by_platform.setdefault(platform, []).append(
            RawMetrics(
                creator_name=name,
                platform=platform,
                handle=name.lower(),
                profile_url=f"https://{platform}.com/{name.lower()}",
                followers=followers,
                avg_likes=5.0 if followers else None,
                avg_comments=1.0 if followers else None,
            )
        )
    return records_by_platform


def new_score(platforms):
    accounts = [
        AccountWeek(platform=p, followers=f, posts=[], has_post_data=False) for p, f in platforms.items()
    ]
    return score_creator_week(accounts, previous_total_followers=None)


def main() -> int:
    # Legacy: needs every creator pooled together per platform for min-max normalization.
    all_records = {}
    for name, platforms, _, _ in CREATORS:
        for platform, records in legacy_score(name, platforms).items():
            all_records.setdefault(platform, []).extend(records)
    legacy_scores = {c.creator_name: c for c in combine_scores(score_all(all_records))}

    rows = []
    for name, platforms, _has_bio, _handles_match in CREATORS:
        legacy = legacy_scores.get(name)
        new = new_score(platforms)
        rows.append((name, legacy.total_score if legacy else None, new.total_score, new.confidence, platforms))

    # Rank both lists (None sorts last).
    by_legacy = sorted(rows, key=lambda r: (r[1] is None, -(r[1] or 0)))
    by_new = sorted(rows, key=lambda r: (r[2] is None, -(r[2] or 0)))
    legacy_rank = {r[0]: i + 1 for i, r in enumerate(by_legacy)}
    new_rank = {r[0]: i + 1 for i, r in enumerate(by_new)}

    lines = [
        "# Sample-check comparison: legacy score vs. new module score",
        "",
        "Brief deliverable #5. This repo has no real creator roster (the",
        "roster and real follower counts live in the sibling `Desktop/ranking`",
        "project this brief was originally written against). The 10 creators,",
        "their follower counts, platform spread, and bio/handle quality below",
        "are taken directly from that project's `data/sample-check.md` --",
        "read-only, never copied wholesale -- and re-scored here with both",
        "this repo's legacy `score_platform`/`combine_scores` (section 8, the",
        "existing README formula) and the new Phase 6 module score",
        "(`module_scoring.score_creator_week`).",
        "",
        "| Legacy rank | New rank | Creator | Legacy score | New score | New confidence |",
        "|---:|---:|---|---:|---:|:--|",
    ]
    for name, legacy_total, new_total, confidence, _platforms in rows:
        lines.append(
            f"| {legacy_rank[name]} | {new_rank[name]} | {name} "
            f"| {legacy_total if legacy_total is not None else '—'} "
            f"| {new_total if new_total is not None else 'n/a'} | {confidence} |"
        )

    lines += [
        "",
        "## An important caveat on this comparison",
        "",
        "The brief's narrative example (\"Gangkar Metok ranking 17th of 118 on",
        "14.7k subscribers\") describes `Desktop/ranking/profile.py`'s scoring,",
        "which this repo never had -- this repo's own legacy `score_platform`/",
        "`combine_scores` (section 8 of its README) is a simpler, already-",
        "different formula: per-platform reach/engagement/growth min-max",
        "normalized against whoever else is in the same run, with no",
        "handle-consistency or bio bonus of any kind. With only 10 creators (not",
        "118) that dramatic a swing doesn't reproduce -- but the same root cause",
        "the brief names, *relative-to-this-batch reach instead of absolute",
        "reach*, is still visible below, just smaller, plus a genuinely new bug",
        "the new formula had to be hardened against.",
        "",
        "## What moved, and why",
        "",
        f"- **Uttam Shetty and Kunsang Tenzing swap places**: legacy has Kunsang",
        f"  ({legacy_rank['Kunsang Tenzing']}th, 2.05k followers) ahead of Uttam",
        f"  ({legacy_rank['Uttam Shetty (Nagaloka Center)']}th, 3.62k followers); the new score reverses that",
        f"  (Uttam {new_rank['Uttam Shetty (Nagaloka Center)']}th, Kunsang {new_rank['Kunsang Tenzing']}th). Legacy renormalizes its",
        "  platform weights across every *resolved* platform, including ones",
        "  that resolved with no public follower count at all -- Uttam has 6 of",
        "  those (vs. Kunsang's 4), each contributing a hard 0 to the weighted",
        "  average, which drags Uttam down below a creator with fewer real",
        "  followers but also fewer zero-count platforms. The new reach module",
        "  only looks at *known* follower totals, so Uttam's extra real reach",
        "  counts for something again.",
        "- **Sonam Choden, Jay Liao, and Chhewang Dorjee have no follower count",
        "  on any platform and no post data**, so reach, engagement, activity,",
        "  and growth are all unscoreable -- only breadth (platform count) is",
        "  left. An earlier version of this scoring code let breadth alone",
        "  carry a total_score by reweighting around it (Sonam Choden scored a",
        "  flat **100** from 4 platforms with zero known followers -- caught",
        "  by running this exact comparison). `combine_modules` now refuses to",
        "  produce a total_score unless reach or engagement is present, so",
        "  these three correctly show `n/a` instead of a fabricated number --",
        "  see `test_combine_modules_refuses_to_score_from_breadth_alone`.",
        "  Legacy, by contrast, has no floor like this and simply min-maxes",
        "  whatever zeros it's given, which is exactly how a confident-looking",
        "  but meaningless number like legacy Jay Liao's old 47 (not shown",
        "  here, from the original Desktop/ranking run) gets produced.",
        "- The four with reach data (Gangkar, Santosh, Arun, Lopon) keep their",
        "  relative order in both systems here -- with no post/engagement data",
        "  in this synthetic snapshot, reach dominates both formulas similarly",
        "  once the zero-count-platform drag (above) is accounted for.",
        "- Every creator's confidence is **low** in this one-off comparison --",
        "  that's a property of the synthetic snapshot (a single week, no post",
        "  history, no prior week to diff growth against), not a claim about",
        "  what real weekly data would look like; `score_confidence`'s actual",
        "  rule is in the README.",
        "",
        "Regenerate with `python3 scripts/sample_check_comparison.py > docs/sample-check-comparison.md`.",
    ]

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
