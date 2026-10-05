# SocialCrawl field mapping

## Known gap: Graph API not yet implemented

Section 4's resolver order puts the Graph API first for Instagram (Business
Discovery) and Facebook (Pages), with SocialCrawl as the fallback. This
document and the current `extractors/socialcrawl.py` only cover the
SocialCrawl side — there's no Graph API resolver yet. Graph API needs a Meta
app id/secret and page access tokens (a materially different, OAuth-style
credential setup from a single API key), and nothing here could be tested
against it without real credentials. Instagram and Facebook currently always
go through SocialCrawl; wiring in Graph API as the preferred first call is
follow-up work, not done in this pass.


Built from SocialCrawl's public docs at `https://www.socialcrawl.dev/docs/*`
(no `SOCIALCRAWL_API_KEY` is available in this environment, so nothing here
was called live). Anything marked **confirm live** should be checked with
`scripts/socialcrawl_discover.py` the first time a real key is available —
that script hits the same free utility endpoints the brief describes
(`/v1/utility/quickstart`, `/v1/utility/endpoints`, `/v1/utility/endpoint`)
and diffs the real response shape against this file.

## What changed from the previous (uncommitted) implementation

The code already in this repo before this change assumed a single
`POST /profiles/batch` call that took a list of accounts and returned all of
them at once (`SOCIALCRAWL_MAX_BATCH` accounts per call). **That endpoint
does not exist.** The real API (`www.socialcrawl.dev`) is one account per
`GET` call, authenticated with an `x-api-key` header (not `Authorization:
Bearer`), and the "50" in the brief's limits is 50 **concurrent in-flight
requests**, not 50 accounts packed into one request body.

**Correction to that correction:** there *is* a real bulk profile endpoint —
`POST /v1/prism/profiles`, up to 50 `(platform, handle)` pairs per call,
1 credit per resolved handle (same total cost as calling individually; the
win is far fewer calls, not fewer credits). Missed in the first pass because
it's filed under "Prism" (composite/bulk endpoints), not under the
per-platform docs pages. `fetch_profiles_bulk()` uses this for the
handle-keyed platforms (Instagram, TikTok, Threads, X) — see "Prism bulk
profiles" below. Facebook and LinkedIn are URL-keyed here and Prism's bulk
`items` shape is `(platform, handle)`, so whether a Facebook page name or
LinkedIn vanity slug behaves the same way in bulk is unconfirmed; they stay
on the single-account path for now. A bounded thread pool
(`SOCIALCRAWL_MAX_CONCURRENCY`, default 50) plus a shared 600/minute rate
limiter still covers those two platforms' profile calls and everyone's posts
calls (posts always stay per-account — see below). `chunk()` is kept for
YouTube's real `channels.list`/`videos.list` 50-ID batching, which is
genuine, and reused to split Prism batches into groups of 50.

## Prism bulk profiles

**Confirmed live 2026-10-05** against a real key (one `tiktok` account) —
the first-pass implementation below was built from docs alone and got the
envelope wrong (assumed `data` was directly a list or `data.items`; it's
actually `data.results`, one nesting level deeper than guessed). Fixed after
a real run against TikTok came back with every account marked
`RESOLUTION_ERROR` despite credits being charged — a real response example
is the fastest way to catch this kind of thing, worth doing before spending
on the full roster next time.

`POST /v1/prism/profiles`, body `{"items": [{"platform", "handle"}, ...],
"include"?: "posts", "since"?: ...}`, up to 50 items. Real response shape:

```json
{
  "success": true, "platform": "prism", "endpoint": "/v1/prism/profiles",
  "data": {
    "results": [
      {
        "index": 0,
        "platform": "tiktok",
        "target": {"platform": "tiktok", "handle": "karmayonten"},
        "fetched_at": "2026-10-05T04:23:57.029Z",
        "status": "ok",
        "data": {
          "author": {
            "id": "76003274469883904", "username": "karmayonten",
            "display_name": "Karma Yonten", "avatar_url": "...",
            "bio": null, "verified": false, "followers": 2, "following": 0,
            "posts_count": 0, "likes_count": 0,
            "url": "https://www.tiktok.com/@karmayonten", "private": false,
            "joined_at": null, "ext": {"business_category": "", "bio_link": null}
          },
          "computed": {"engagement_rate": 0, "language": null, "content_category": null, "estimated_reach": null}
        },
        "cost": 1
      }
    ],
    "summary": {"total": 1, "ok": 1, "not_found": 0, "unsupported": 0, "error": 0, "deferred": 0, "coverage": 1, "credits_charged": 1, "credits_refunded": 0},
    "legs": [{"endpoint": "/v1/tiktok/profile", "status": 200, "credits_used": 1, "latency_ms": 1410, "error": null}]
  },
  "credits_used": 1, "request_id": "...", "cached": false, "credits_remaining": 94
}
```

Each row's profile is `row.data.author` (not `row.author`), and `followers`
lives directly on `author` (matching the alias list already used for the
single-account path). `account_created_at` should read `author.joined_at`
as well as `created_at` — also fixed from this example (it was `null` here,
but the field name is now confirmed). `fetch_profiles_bulk()` matches each
row back to its request by the row's own `index` field (confirmed present
and reliable), not array position, and treats `status != "ok"` as a
failure (`not_found` → our `not-found`, anything else → `error`) — still
**unconfirmed**: the exact shape of a `not_found`/`error`/`unsupported`/
`deferred` row beyond `status` itself (this example only shows `ok`).

Prism's own platform name for X is `"x"` (not `"twitter"`, which is what the
single-account `/twitter/profile` endpoint still uses — two different naming
conventions for the same platform on SocialCrawl's own side).

`include: "posts"` adds a `posts` block to each row with "that handle's
first page of posts" (and drops the per-call batch size to 25 when used) —
not the paginated 90-day/100-post window this pipeline needs, so posts are
still fetched through the per-account endpoints below regardless of
platform. This might be worth revisiting if credit cost ever becomes the
bottleneck instead of call count: a single page is often most of a week's
new posts for a low-volume account, which could reduce a fetch's additional
posts call entirely — **not implemented, worth a second look once there's a
live key**.

## Base URL, auth, envelope

- Base URL: `https://www.socialcrawl.dev/v1` (was `https://api.socialcrawl.io/v1` —
  wrong host, corrected).
- Header: `x-api-key: <SOCIALCRAWL_API_KEY>` (was `Authorization: Bearer …` — wrong
  scheme, corrected).
- Every response: `{success, platform, endpoint, data, credits_used,
  credits_remaining, request_id, cached}`. Error responses replace `data`
  with `{error: {type, message, status, doc_url, retryable, details}}`.
- Retry only on `error.retryable == true`, which the docs say always lines up
  with HTTP status `429` (`RATE_LIMITED`, `CONCURRENCY_LIMIT`), `500`
  (`INTERNAL_ERROR`), `502` (`UPSTREAM_ERROR`), `503` (`SERVICE_UNAVAILABLE`).
  `HttpClient`'s existing `RETRYABLE_STATUS = {429, 500, 502, 503, 504}` already
  covers this by status code alone, so the extractor doesn't need to parse
  `error.type` itself. Max 4 tries, exponential backoff + jitter (section 4).
- `404 RESOURCE_NOT_FOUND` carries `error.details.reason`: `account_gone` or
  `handle_unresolved` → our `not-found`; a 200 response with `data.author.private
  == true` → our `private` (confirmed for Instagram in the docs; applied to every
  platform defensively since the docs don't rule it out elsewhere — **confirm
  live** per platform).
- Pagination: `data.pagination.{next_cursor, has_more}` (send `next_cursor` back
  as the next call's `cursor`). Field name for the cursor **param** itself
  varies by platform (see table) — **confirm live**.
- `credits_used` is summed across every call in a run and logged once at the
  end (section 4).

## Per-platform endpoints and field mapping

Profile calls answer `followers`/`verified`/etc.; posts calls answer the
section-5 post list. `type` below is our normalized post type
(`video`/`short`/`image`/`carousel`/`text`/`other`).

### Instagram

| Our field | Source |
|---|---|
| Profile endpoint | `GET /instagram/profile?handle=` (1 credit) |
| Posts endpoints | `GET /instagram/profile/posts?handle=&next_max_id=` (images/carousels, 1 credit) **and** `GET /instagram/profile/reels?handle=&max_id=` (video, has views — brief: "views only exist on Reels") |
| `followers` | `data.author.followers` |
| `verified` | `data.author.verified` |
| `bio` | `data.author.biography` |
| `avatar_url` | `data.author.avatar` |
| `category` | `data.author.ext` (business category, if present) — **confirm live** |
| post `id`/`url` | `post.id` / `post.url` |
| post `title` | `post.caption` (first 140 chars) |
| post `type` | `carousel` if multiple media, else `image`; reels endpoint → `short` if duration ≤ 60s else `video` |
| post `views`/`likes`/`comments` | `engagement.views` (reels only), `engagement.likes`, `engagement.comments` |
| post `shares` | not returned by the list endpoints (`/post/stats` has it at +5 credits/post — too expensive for 100 posts/account/week) → always `null` |
| post `thumbnail_url`/`duration_s` | `post.media_urls` thumbnail, reel `ext` duration — **confirm live** |

### Facebook (Pages only, per the brief)

| Our field | Source |
|---|---|
| Profile endpoint | `GET /facebook/profile?url=` (1 credit) |
| Posts endpoint | `GET /facebook/profile/posts?url=&cursor=` (1 credit) |
| `followers` | `data.follower_count` |
| `verified` | `data.verified` |
| post `id`/`type`/`published_at` | `post.id` / `post.type` / `post.published_time` |
| post `likes`/`comments`/`shares`/`views` | `post.likes` / `post.comments` / `post.shares` / `post.views` (views "when available on permalink" — often `null`) |

### TikTok

| Our field | Source |
|---|---|
| Profile endpoint | `GET /tiktok/profile?handle=` (1 credit) |
| Posts endpoint | `GET /tiktok/profile/videos?handle=&max_cursor=` (1 credit) |
| `followers` | `data.author.followers` |
| `verified` | `data.author.verified` |
| post engagement | views/likes/comments/shares — exact nesting not shown in the docs excerpt (likely `engagement.*`, matching every other platform) — **confirm live** |
| post `duration_s` | video length, field name unconfirmed — **confirm live** |

### Threads

| Our field | Source |
|---|---|
| Profile endpoint | `GET /threads/profile?handle=` (1 credit) |
| Posts endpoint | `GET /threads/user/posts?handle=&limit=10&include=engagement` (cost scales with `limit`, not flat — log the real `credits_used`, don't assume 1. **Confirmed live 2026-10-05**: `limit=50` triggered a 402 Payment Required on a low-credit account; dropped to 10. Exact cost-per-limit ratio still unconfirmed) |
| `followers` | `data.author.followers` |
| `verified` | `data.author.verified` |
| `bio` | `data.author.bio` |
| post `id`/`text`/`timestamp` | `post.id` / `post.text` (→ `title`, first 140 chars) / `post.timestamp` |
| post `views`/`likes`/`shares` | `engagement.views` (needs `include=engagement`), `engagement.likes`, `engagement.shares` |
| post `comments` | not shown in the docs excerpt for the list endpoint → `null` unless confirmed live |
| post `thumbnail_url` | `post.content.thumbnail_url` |

### X (Twitter)

SocialCrawl's own path is still `/twitter/...`; our platform key stays `x` to
match the brief and the validator's domain table (`x.com`/`twitter.com`).

| Our field | Source |
|---|---|
| Profile endpoint | `GET /twitter/profile?handle=` (1 credit) |
| Posts endpoint | `GET /twitter/user/tweets?handle=&cursor=` (1 credit/page) |
| `followers` | `data.author.followers_count` (this platform uses `_count` suffixes, not the bare `followers`/`following` every other platform uses) |
| `verified` | `data.author.verified` |
| `bio` | `data.author.description` |
| post `id`/`text`/`timestamp` | `post.id` / `post.content.text` / `post.timestamp` |
| post `views`/`likes`/`comments`/`shares` | `engagement_metrics.views`, `.likes`, `.replies` (→ comments), `.retweets` (→ shares) |

### LinkedIn

Expensive relative to the others (5 credits per call, every endpoint) — only
4 accounts today per the brief, so cost isn't the concern reach is elsewhere.

| Our field | Source |
|---|---|
| Profile endpoint | `GET /linkedin/profile?url=` (5 credits) — company pages use `GET /linkedin/company?url=` instead |
| Posts endpoint | `GET /linkedin/profile/posts?url=&limit=` (5 credits) / `GET /linkedin/company/posts?company_id=` for pages |
| `followers` | `data.follower_count` (people) / company equivalent — **confirm live**, the docs only show `connection_count` clearly for people profiles |
| post `published_at` | `post.published_at` |
| post engagement | per-post reactions/comments/shares each need their **own** paid call
  (`/post/reactions`, `/post/comments`, 5-50 credits) — not pulled given the
  volume/cost tradeoff; `likes`/`comments`/`shares` stay `null` for LinkedIn
  until that's reconsidered |

## Rate limits recap (section 4, confirmed in the docs)

600 requests/minute and 50 concurrent requests per key, most calls 1 credit,
cache hits 0 credits, paginate via `next_cursor`/`has_more`. Retry only
`429`/`500`/`502`/`503` (5-try cap incl. the first attempt = 4 retries), each
with exponential backoff + random jitter.
