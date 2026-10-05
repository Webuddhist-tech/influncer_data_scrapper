# Social Media Ranking (InfluenceRank)

Python package for a weekly social-media ranking pipeline for the Buddhist
Creator Convention. It reads a creator list (Excel/CSV with Linktree URLs),
extracts and validates profile links, pulls metrics (SocialCrawl for
Instagram, Facebook, TikTok, Threads, and X; the Graph API for Instagram
Business/Creator accounts and Facebook Pages; YouTube Data API v3 for
YouTube; SocialCrawl again for LinkedIn), scores creators with an explainable
module breakdown, and publishes an allowlisted public export.

Everything under `data/` is **private** and lives in this repo. Only
`export/public/` — built by an explicit field allowlist — is pushed to the
public site repo (`Webuddhist-tech/influencer-data`, a Vite + React site on
GitHub Pages that reads only those JSON files and never calls an API).

## Design notes (where this repo diverges from the brief)

The brief this was built from was written against a *different*, earlier
project (`Desktop/ranking`, with `data/creators.json`, `c<12hex>` creator
IDs, `overrides.json`, a `socials/` resolver package, `profile.py`) that
isn't this repo. Rather than rewrite this already-working, tested pipeline
around that different file layout, the brief's intent was re-mapped onto
this repo's existing CSV-per-stage design. The notable choices:

- **No separate ID layer.** A creator's **slug** (derived once from their
  name, made unique, then persisted — `overrides.assign_slugs`) is the
  stable public identity key everywhere the brief would use `id`. Internally,
  `creator_name` is still the join key across `creator_profiles.csv`,
  exactly as before.
- **`data/overrides.json`** is new: manual fixes only (`slug`,
  `confirmation`, `merged_into`, `language`, `bio`, `avatar_url`), keyed by
  `creator_name`. `data/creator_profiles.csv` is still fully generated and
  rewritten every run — never hand-edit it.
- **`confirmation`** is an optional sheet column (`confirmation`/`status`),
  defaulting to `Pending` when absent, overridable in `overrides.json`. Only
  `Confirmed` creators reach the public export; everyone is still scored.
- **The SocialCrawl integration was corrected, not just extended.** The code
  already in this repo assumed a `POST /profiles/batch` multi-account
  endpoint that doesn't exist on the real API (`www.socialcrawl.dev`, not
  `api.socialcrawl.io`) — it's one account per `GET` call, authenticated
  with `x-api-key`, with a 50-concurrent-request and 600/minute ceiling (not
  a batch size). See `docs/socialcrawl-fields.md` for the full writeup and
  the endpoint/field mapping per platform.
- **The weekly workflow used to push *everything*** (`creator_profiles.csv`,
  raw snapshots, internal flags) straight into the public site repo with no
  filtering. That's fixed: `data/` now stays in this repo; only
  `export/public/` crosses the boundary.
- **Sample-check (deliverable #5)** uses the real `data/sample-check.md` from
  `Desktop/ranking` as a read-only reference for realistic numbers (never
  copied wholesale) to build a synthetic 10-creator comparison, since this
  repo has no real roster of its own. See `docs/sample-check-comparison.md`
  and `scripts/sample_check_comparison.py`.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python 3.9+. Import as `social_media_ranking`. The CLI command is `social-media-ranking`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `SOCIALCRAWL_API_KEY` | SocialCrawl API key, sent as `x-api-key` |
| `SOCIALCRAWL_BASE_URL` | Default `https://www.socialcrawl.dev/v1` |
| `SOCIALCRAWL_MAX_CONCURRENCY` | In-flight requests, capped at 50 regardless (default `50`) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key |
| `YOUTUBE_BASE_URL` | Default `https://www.googleapis.com/youtube/v3` |
| `SOCIAL_MEDIA_RANKING_REQUEST_TIMEOUT` | HTTP timeout in seconds (default `30`) |
| `SOCIAL_MEDIA_RANKING_MAX_RETRIES` | Retry count for the pipeline's general HTTP calls (default `3`; SocialCrawl itself always retries up to 4 times, per its own docs) |
| `CREATOR_SHEET_CSV_URL` | The roster Google Sheet's CSV export URL (see "Creator roster source" below). Unset means `extract-links` needs an explicit `--input creators.xlsx` instead. |

YouTube is **not** sent through SocialCrawl. (The brief names this variable
`SOCIAL_CRAWL_API_KEY`; kept as `SOCIALCRAWL_API_KEY` since that's what this
repo already used everywhere.)

## Creator roster source

Same mechanism as the sibling `Desktop/ranking` project: the roster lives in
a Google Sheet, downloaded fresh (as CSV) on every `extract-links` run —
not a file someone has to remember to commit. Organizers add a creator by
either editing the Sheet directly or (better) through a Google Form whose
responses land as new rows in it.

**One-time setup** (there's no API for creating these from here — this is a
few minutes in your own Google account):

1. **Create the Sheet.** Columns, in any order, with these exact header
   names (case-insensitive) — `read_creator_sheet` looks for each by name:
   `Name`, `Linktree URL` (anything containing "link" or "url" also
   matches), and optionally `Confirmation`, `Language`, `Email`, `WhatsApp`.
   `Confirmation`/`Language` can also be filled in later by an organizer —
   leave them blank and every new row defaults to `Pending`/no language.
2. **Get the CSV export URL.** File → Share → "Publish to web", or just
   build it from the Sheet's URL: copy the ID between `/d/` and `/edit` in
   your browser's address bar, and the `gid=` number for the specific tab,
   then use
   `https://docs.google.com/spreadsheets/d/<SHEET_ID>/export?format=csv&gid=<GID>`.
   Put that in this repo's `CREATOR_SHEET_CSV_URL` secret (Settings → Secrets
   and variables → Actions) for the weekly workflow, and in your own shell
   for local runs.
3. **Add a Google Form for new-creator submissions** (Tools → Create a Form,
   from the Sheet's menu — this is the "add a creator" form, no custom code
   needed). Ask for: Name, Linktree URL, Email, WhatsApp/phone, Language. In
   the Form's settings, point its responses at this same Sheet ("Link to
   Sheet"), and make sure the response columns it creates use the same
   header names as above (rename the Form's auto-generated column headers in
   the Sheet if they don't match — `read_creator_sheet` matches by header
   name, not position). Share the Form's URL with whoever needs to submit a
   creator; you never need to touch this repo for a submission to show up —
   it's picked up on the next `extract-links` run (weekly, or on demand).
4. Email/WhatsApp are for organizers only — `export_public.py`'s allowlist
   never references them, enforced by `tests/test_export_public.py`'s
   contact scan.

Still want a committed file instead? Leave `CREATOR_SHEET_CSV_URL` unset and
run `extract-links --input creators.xlsx` as before — both paths work, the
Sheet is just no longer a hard requirement.

## CLI

Each stage is its own subcommand so a failed week can be resumed without re-scraping everything.

```bash
social-media-ranking extract-links --input creators.xlsx --output data/creator_profiles.csv
social-media-ranking extract-links --input creators.xlsx --refresh   # bypass the 14-day page cache
social-media-ranking validate-links --input data/creator_profiles.csv
social-media-ranking extract-social --platform all --input data/creator_profiles.csv --output data/raw/
social-media-ranking extract-social --platform instagram   # re-run one platform
social-media-ranking extract-social --dry-run              # print call counts, spend no credits
social-media-ranking extract-social --only "Lotus Sangha"   # by creator_name or slug
social-media-ranking score --input data/raw/ --output data/scores/        # legacy score (section 8, kept for comparison)
social-media-ranking weekly-score --week 2026-09-28 --rebuild              # Phase 6 module score + data/history/
social-media-ranking export-public --week 2026-09-28                       # build export/public/ (section 7)
social-media-ranking publish --input data/scores/latest.csv --no-push
social-media-ranking run-weekly --input creators.xlsx --week 2026-09-28 --only NAME --rebuild --no-push
```

`run-weekly` chains extract (or validate) → extract-social → legacy score →
weekly-score → export-public → publish. It refuses to export or publish (and
exits non-zero) when more than 30% of accounts are unresolved this week —
more likely an outage or a bad key than reality.

`--data-root` (default `data`) and `--repo-root` (default `.`) apply to all
commands. `--week`/`--date` override the computed week label; `--rebuild`
allows overwriting a *past* week's history (refused otherwise — re-running
the current week is always safe and gives the same files).

## Data layout

**Private (this repo, committed):**

| Path | Contents |
| --- | --- |
| `data/raw-sheet.csv` | The last downloaded copy of the roster Sheet (or the `--input` file), cached so a failed download falls back to it |
| `data/creator_profiles.csv` | Creator, platform, handle, URL, status, confirmation, slug, merged_into, language, avatar_url, email, whatsapp |
| `data/overrides.json` | Manual fixes only — never hand-edit `creator_profiles.csv` |
| `data/raw/{platform}/{YYYY-MM-DD}.csv` | That week's raw per-platform metrics |
| `data/cache/posts/{platform}/{hash}.json` | Per-account post cache (section 5) — last 100 posts / 90 days, merged by id each run |
| `data/cache/pages/{hash}.html` | Cached Linktree page fetches (14-day TTL) — makes a full weekly `extract-links` cheap |
| `data/scores/{YYYY-MM-DD}.csv`, `data/scores/latest.csv` | Legacy per-platform score (section 8), kept for comparison |
| `data/history/{YYYY-MM-DD}.csv` | One row per creator × platform: `week, creator_name, platform, resolved, carried_over, followers, followers_source, posts_90d, engagement_rate, total_score`. Past weeks are never rewritten without `--rebuild`. |
| `data/latest/summary.json`, `data/latest/{platform}.json` | Legacy dashboard rollups |
| `logs/unresolved/{YYYY-MM-DD}.csv` | Failed validation or extraction, with reason |

`data/history/*.csv` uses `creator_name`, not a hex id, as the brief's `id`
column (see Design notes).

**Public export (allowlist only, `export/public/`, git-ignored here, pushed
to the site repo's `data/latest/`):** `summary.json` and
`creators/{slug}.json`, shaped exactly per the brief's section 7, plus
`avatars/{slug}.jpg`. Built from an explicit field allowlist in
`export_public.py` — nothing from the roster sheet, email, WhatsApp, notes,
or internal flags can reach it. `tests/test_export_public.py` enforces this
with a scan for emails, phone numbers, and `wa.me` links.

## Scoring

**Legacy (section 8 of the original design, kept only for comparison):**
`PlatformScore = 0.50×Reach + 0.35×Engagement + 0.15×Growth`, each min-max
normalized against other creators on that platform *this run*, then rolled
into a total weighted by platform (YouTube 30%, Instagram 25%, Facebook 15%,
TikTok 15%, LinkedIn 10%, Threads 5%; X isn't in this legacy weighting — it's
new since this formula was written).

**New (Phase 6, `module_scoring.py`) — this is the real score now.** The
legacy score's problem: completeness/consistency were baked into the
ranking itself, so a creator with real reach but untidy data lost to a
smaller, tidier one (see `docs/sample-check-comparison.md`). The fix: score
only real influence, move data-quality signals into `confidence` instead.

`total_score` (0–100) is a weighted sum of five modules, each independently
0–100:

| Module | Weight | Formula |
| --- | --- | --- |
| Reach | 35 | `min(100, 100 × log10(total followers) / 6)` — 1M followers = 100 |
| Engagement | 30 | `min(100, median post engagement rate / 0.06 × 100)` — 6% = 100 |
| Activity | 20 | `min(100, posts per week over 90 days / 3 × 100)` — 3/week = 100 |
| Growth | 10 | `clamp(50 + growth / 0.02 × 50, 0, 100)` — +2%/week = 100, flat = 50 |
| Breadth | 5 | platform count: 1→12, 2→18, 3→22, 4+→25 points, rescaled to 0–100 |

Per-post engagement rate is `(likes + comments + shares) / views`, or `/
followers` when views aren't public; a post with every part `null` is
skipped, not scored as 0.

**Missing modules** (no post data, first week with no growth baseline, …)
are dropped and the rest re-weighted proportionally — *except* a
`total_score` is never produced from activity/growth/breadth alone: without
either Reach or Engagement, there's no real signal to score, so it comes
back `null` rather than a number that looks precise but isn't (caught via
`docs/sample-check-comparison.md` — an earlier version let breadth alone
produce a flat 100).

**Confidence** (`high`/`medium`/`low`, `module_scoring.compute_confidence`):

- **low** if anything is severe: 2+ modules missing, 2+ accounts carried
  over, or at most half of this creator's platforms resolved this week.
- **medium** if anything is merely off: any module missing, any account
  carried over, or partial-but-above-the-severe-line coverage.
- **high** otherwise.

**Carry-over:** an unresolved account reuses last week's followers/posts
with `carried_over: true`, for at most 2 consecutive weeks — the 3rd
unresolved week in a row drops it for real. A creator with zero
resolved-or-carried accounts isn't published. `merged_into` creators are
skipped everywhere.

**Periods** (`week`/`month`/`all`, each `[current, previous]`): `week` is
this week vs. last week's total; `month` is the mean of the last 4 weekly
totals vs. the 4 before; `all` is the mean of every week vs. all-but-this-week.
`null` wherever there isn't enough history yet.

## New resolvers / platform coverage

TikTok, Threads, X (`x.com`/`twitter.com` — SocialCrawl's own path is still
`/twitter/...`), LinkedIn, Instagram, and Facebook all go through SocialCrawl
today. The brief's resolver order puts the Graph API first for Instagram
(Business Discovery) and Facebook (Pages), with SocialCrawl as the fallback
— **that part isn't implemented**: Graph API needs Meta app credentials and
page access tokens, a different setup from a single API key, and there was
nothing to test it against here. See `docs/socialcrawl-fields.md`'s "Known
gap" section. Endpoint choice and field mapping per platform for the
SocialCrawl side — including what's still unconfirmed without a live key —
is in that same doc. Run `SOCIALCRAWL_API_KEY=...
python3 scripts/socialcrawl_discover.py` once a real key exists to
confirm/update that mapping against live responses.

## Extractor behavior

- Linktree (and similar) pages: scrape outbound links; merch/non-social URLs are ignored, not errors. Dead pages are logged and skipped. Each page is also read for its own embedded `__NEXT_DATA__` JSON blob (same technique as Desktop/ranking's `extract.py`) to pull the creator's avatar (Linktree's own hosted photo — the one image reliably available regardless of platform) and a language tag, with no extra fetch. Pages are cached 14 days (`page_cache.py`) so a full re-extract is cheap enough to run every week.
- Profile vs content URLs: video/post/reel/story paths are rejected. Tracking params are stripped. Shorteners are resolved then reclassified.
- SocialCrawl profiles: one account per `GET` call by default. There's also
  a real bulk endpoint for Instagram/TikTok/Threads/X, `POST
  /v1/prism/profiles` (up to 50 accounts/call, same 1 credit/account either
  way — batching cuts call count, not credit cost), implemented and tested
  in `fetch_profiles_bulk()` but **off by default**
  (`SOCIALCRAWL_USE_BULK_PROFILES=true` to opt in) after a live run on a
  small-credit account found its behavior costlier/less predictable in
  practice than expected — see `docs/socialcrawl-fields.md` for the
  specifics. Posts are always per-account, bounded by a thread pool +
  shared rate limiter at the documented 50-concurrent / 600-per-minute
  ceiling. Retries 429/5xx up to 4 times with exponential backoff + jitter.
  Total credits used are logged once per run.
- YouTube: `channels.list` / `videos.list` in batches of 50; `@handle` → channel ID is cached on the profile row. Posts paginate via `playlistItems` up to the 90-day window or the 100-post cap, whichever comes first.
- Posts (every platform): cached per account under `data/cache/posts/`,
  merged by id and re-capped each run, so a rerun only fetches what's new.

## Tests

```bash
PYTHONPATH=src pytest
```

Validator fixtures cover valid profiles, content URLs, UTM stripping,
shorteners, slash/case normalization, non-social ignores, and empty pages
(now including X). Extractor tests cover the real per-account SocialCrawl
API and YouTube's post pagination/duration parsing. `test_module_scoring.py`
covers each module's scale/clamp, re-weighting, confidence, carry-over
cutoff, and period math. `test_export_public.py` covers the
confirmed-only/no-slug/atomic-write behavior and the no-contacts scan.
`test_overrides.py` covers slug derivation, uniqueness, and override
precedence.

## Excel input

A `.xlsx` or `.csv` with a name column (`creator`, `name`, …) and a bio-link
column (`linktree`, `url`, …). Optional `confirmation`/`status` and
`language` columns are picked up if present. If headers are missing, the
first two columns are used.

## GitHub Actions

- `.github/workflows/weekly.yml` — Mondays 02:00 UTC + manual dispatch.
  `extract-links` → `extract-social` → legacy `score` → `weekly-score` →
  `export-public` → archive `data/raw/` as a 90-day artifact → commit
  `data/` here as `github-actions[bot]` → replace
  `Webuddhist-tech/influencer-data`'s `data/latest/` with `export/public/`
  using `SITE_REPO_TOKEN`. `concurrency: weekly` — never two runs at once.
  There's no separate monthly workflow: every Linktree page is cached for 14
  days (`page_cache.py`), so re-scraping the whole sheet every week — not
  just re-validating existing profiles — is cheap, and a brand-new row in
  `creators.xlsx` is never more than a week from showing up. Pass `refresh`
  on a manual dispatch to bypass the cache and force a real re-fetch of
  every page.
