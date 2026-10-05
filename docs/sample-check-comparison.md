# Sample-check comparison: legacy score vs. new module score

Brief deliverable #5. This repo has no real creator roster (the
roster and real follower counts live in the sibling `Desktop/ranking`
project this brief was originally written against). The 10 creators,
their follower counts, platform spread, and bio/handle quality below
are taken directly from that project's `data/sample-check.md` --
read-only, never copied wholesale -- and re-scored here with both
this repo's legacy `score_platform`/`combine_scores` (section 8, the
existing README formula) and the new Phase 6 module score
(`module_scoring.score_creator_week`).

| Legacy rank | New rank | Creator | Legacy score | New score | New confidence |
|---:|---:|---|---:|---:|:--|
| 1 | 1 | Gangkar Metok | 50.0 | 73.3 | low |
| 2 | 2 | SANTOSH SAKHARAM AMBHORE | 42.16 | 70.6 | low |
| 3 | 3 | Arun Sonune | 40.76 | 70.0 | low |
| 4 | 4 | Lopon Jurmay Karma | 39.59 | 69.5 | low |
| 6 | 6 | Paudel Dhruba | 32.6 | 64.1 | low |
| 7 | 5 | Uttam Shetty (Nagaloka Center) | 31.79 | 64.4 | low |
| 5 | 7 | Kunsang Tenzing | 35.0 | 60.8 | low |
| 8 | 8 | Sonam Choden | — | n/a | low |
| 9 | 9 | Jay Liao, Yeshe gyatso | — | n/a | low |
| 10 | 10 | Chhewang Dorjee | — | n/a | low |

## An important caveat on this comparison

The brief's narrative example ("Gangkar Metok ranking 17th of 118 on
14.7k subscribers") describes `Desktop/ranking/profile.py`'s scoring,
which this repo never had -- this repo's own legacy `score_platform`/
`combine_scores` (section 8 of its README) is a simpler, already-
different formula: per-platform reach/engagement/growth min-max
normalized against whoever else is in the same run, with no
handle-consistency or bio bonus of any kind. With only 10 creators (not
118) that dramatic a swing doesn't reproduce -- but the same root cause
the brief names, *relative-to-this-batch reach instead of absolute
reach*, is still visible below, just smaller, plus a genuinely new bug
the new formula had to be hardened against.

## What moved, and why

- **Uttam Shetty and Kunsang Tenzing swap places**: legacy has Kunsang
  (5th, 2.05k followers) ahead of Uttam
  (7th, 3.62k followers); the new score reverses that
  (Uttam 5th, Kunsang 7th). Legacy renormalizes its
  platform weights across every *resolved* platform, including ones
  that resolved with no public follower count at all -- Uttam has 6 of
  those (vs. Kunsang's 4), each contributing a hard 0 to the weighted
  average, which drags Uttam down below a creator with fewer real
  followers but also fewer zero-count platforms. The new reach module
  only looks at *known* follower totals, so Uttam's extra real reach
  counts for something again.
- **Sonam Choden, Jay Liao, and Chhewang Dorjee have no follower count
  on any platform and no post data**, so reach, engagement, activity,
  and growth are all unscoreable -- only breadth (platform count) is
  left. An earlier version of this scoring code let breadth alone
  carry a total_score by reweighting around it (Sonam Choden scored a
  flat **100** from 4 platforms with zero known followers -- caught
  by running this exact comparison). `combine_modules` now refuses to
  produce a total_score unless reach or engagement is present, so
  these three correctly show `n/a` instead of a fabricated number --
  see `test_combine_modules_refuses_to_score_from_breadth_alone`.
  Legacy, by contrast, has no floor like this and simply min-maxes
  whatever zeros it's given, which is exactly how a confident-looking
  but meaningless number like legacy Jay Liao's old 47 (not shown
  here, from the original Desktop/ranking run) gets produced.
- The four with reach data (Gangkar, Santosh, Arun, Lopon) keep their
  relative order in both systems here -- with no post/engagement data
  in this synthetic snapshot, reach dominates both formulas similarly
  once the zero-count-platform drag (above) is accounted for.
- Every creator's confidence is **low** in this one-off comparison --
  that's a property of the synthetic snapshot (a single week, no post
  history, no prior week to diff growth against), not a claim about
  what real weekly data would look like; `score_confidence`'s actual
  rule is in the README.

Regenerate with `python3 scripts/sample_check_comparison.py > docs/sample-check-comparison.md`.
