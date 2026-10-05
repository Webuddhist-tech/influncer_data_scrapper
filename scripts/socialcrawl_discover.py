#!/usr/bin/env python3
"""Confirm docs/socialcrawl-fields.md against a live SocialCrawl key.

Run once SOCIALCRAWL_API_KEY is set, before trusting the mapping doc in a
real weekly run:

    SOCIALCRAWL_API_KEY=... python3 scripts/socialcrawl_discover.py

Hits the free utility endpoints (0 credits) for every platform in
config.SOCIALCRAWL_PLATFORMS, then does one real 1-credit profile call per
platform against a small known-public handle so a real example response can
be saved under tests/fixtures/socialcrawl/. Nothing here is required for the
pipeline to run — it's a one-time (or occasional) sanity check.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_media_ranking.config import Config  # noqa: E402
from social_media_ranking.extractors.socialcrawl import (  # noqa: E402
    PLATFORM_ENDPOINTS,
    PRISM_BULK_PLATFORMS,
    PRISM_PLATFORM_NAMES,
)
from social_media_ranking.http import HttpClient  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "socialcrawl"

# A small, stable, public handle per platform purely to sanity-check field
# names — not part of the real creator roster.
SAMPLE_HANDLES = {
    "instagram": "nasa",
    "facebook": "nasa",
    "tiktok": "nasa",
    "threads": "nasa",
    "x": "nasa",
    "linkedin": "https://www.linkedin.com/company/nasa",
}


def main() -> int:
    config = Config.from_env()
    if not config.socialcrawl_api_key:
        print("SOCIALCRAWL_API_KEY is not set — nothing to confirm.", file=sys.stderr)
        return 1

    client = HttpClient(config.request_timeout, config.max_retries)
    headers = {"x-api-key": config.socialcrawl_api_key}

    for platform in ("instagram", "facebook", "tiktok", "threads", "x", "linkedin"):
        print(f"== {platform} ==")
        try:
            spec = client.get_json(
                f"{config.socialcrawl_base_url}/utility/endpoint",
                params={"id": f"{PLATFORM_ENDPOINTS[platform]['api_platform']}/profile"},
                headers=headers,
            )
            print(json.dumps(spec, indent=2)[:2000])
        except Exception as exc:  # noqa: BLE001 - discovery output only
            print(f"  utility/endpoint failed: {exc}")

        handle = SAMPLE_HANDLES[platform]
        param = PLATFORM_ENDPOINTS[platform]["param"]
        try:
            profile = client.get_json(
                f"{config.socialcrawl_base_url}{PLATFORM_ENDPOINTS[platform]['profile']}",
                params={param: handle},
                headers=headers,
            )
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            out = FIXTURE_DIR / f"{platform}_profile.json"
            out.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")
            print(f"  saved {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"  profile call failed: {exc}")

    print("== prism/profiles (bulk) ==")
    items = [
        {"platform": PRISM_PLATFORM_NAMES.get(p, p), "handle": SAMPLE_HANDLES[p]}
        for p in sorted(PRISM_BULK_PLATFORMS)
    ]
    try:
        bulk = client.post_json(
            f"{config.socialcrawl_base_url}/prism/profiles", {"items": items}, headers=headers
        )
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        out = FIXTURE_DIR / "prism_profiles_bulk.json"
        out.write_text(json.dumps(bulk, indent=2, sort_keys=True), encoding="utf-8")
        print(f"  saved {out} -- check row order/shape matches fetch_profiles_bulk()'s assumptions")
    except Exception as exc:  # noqa: BLE001
        print(f"  prism/profiles call failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
