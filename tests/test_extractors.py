"""Section 4 SocialCrawl calls and section 5 YouTube/post collection behaviour."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
import requests

from social_media_ranking.config import Config
from social_media_ranking.extractors.socialcrawl import (
    SocialCrawlExtractor,
    chunk,
    estimate_calls,
    estimate_credits,
    parse_post,
    parse_profile,
)
from social_media_ranking.extractors.youtube import YouTubeExtractor
from social_media_ranking.models import RESOLUTION_NOT_FOUND, RESOLUTION_OK, RESOLUTION_PRIVATE, CreatorProfile


def profiles(platform: str, n: int) -> List[CreatorProfile]:
    return [
        CreatorProfile(f"creator{i}", platform, f"handle{i}", f"https://{platform}.com/handle{i}")
        for i in range(n)
    ]


def test_chunk_packs_to_the_configured_ceiling():
    assert [len(c) for c in chunk(list(range(250)), 100)] == [100, 100, 50]


def test_chunk_rejects_a_zero_batch_size():
    with pytest.raises(ValueError):
        chunk([1, 2], 0)


def test_estimate_calls_is_per_account_by_default_bulk_is_opt_in():
    # Bulk profiles are off by default (2026-10-05) -- 120 instagram accounts
    # costs 120 profile calls, not ceil(120/50), unless explicitly enabled.
    assert estimate_calls(profiles("instagram", 120), "instagram") == 120 + 120 * 2
    assert estimate_calls(profiles("instagram", 120), "instagram", use_bulk_profiles=True) == 3 + 120 * 2
    assert estimate_calls(profiles("facebook", 5), "facebook") == 5 * (1 + 1)


def test_fetch_uses_the_per_account_path_by_default_even_for_bulk_platforms():
    # Regression: a real run hit this exact gap -- bulk must be opt-in.
    client = FakeSocialCrawlClient({"/instagram/profile": [{"success": True, "data": {"author": {"followers": 7}}}]})
    config = Config(socialcrawl_api_key="k")  # socialcrawl_use_bulk_profiles defaults False
    metrics = SocialCrawlExtractor(config, client=client).fetch(profiles("instagram", 1), "instagram")
    assert metrics[0].followers == 7
    assert not any(c["path"] == "/prism/profiles" for c in client.calls)


def test_estimate_credits_weights_linkedin_five_times_higher():
    # Instagram: 1 (profile) + 1 (posts) + 1 (reels) = 3 credits/account.
    assert estimate_credits(profiles("instagram", 4), "instagram") == 4 * 3
    # LinkedIn: 5 (profile) + 5 (posts) = 10 credits/account.
    assert estimate_credits(profiles("linkedin", 4), "linkedin") == 4 * 10


def test_parse_profile_maps_common_fields():
    profile = profiles("instagram", 1)[0]
    metrics = parse_profile(profile, {"followers": 1000, "verified": True})
    assert metrics.followers == 1000
    assert metrics.verified is True
    assert metrics.resolution_status == RESOLUTION_OK
    assert metrics.followers_source == "socialcrawl"


def test_parse_profile_uses_x_specific_follower_alias():
    profile = profiles("x", 1)[0]
    metrics = parse_profile(profile, {"followers_count": 4200})
    assert metrics.followers == 4200


def test_parse_post_classifies_type_from_media_type_and_duration():
    profile = profiles("instagram", 1)[0]
    reel = parse_post("instagram", profile, {"id": "1", "type": "reel", "duration": 45, "engagement": {"views": 10}}, "image")
    assert reel.type == "short"
    video = parse_post("instagram", profile, {"id": "2", "type": "video", "duration": 180}, "image")
    assert video.type == "video"
    # No explicit type on the item -> falls back to the endpoint's default_type.
    untyped = parse_post("instagram", profile, {"id": "3"}, "image")
    assert untyped.type == "image"


def test_parse_post_reads_engagement_and_caption_aliases():
    profile = profiles("x", 1)[0]
    post = parse_post(
        "x",
        profile,
        {
            "id": "1",
            "content": {"text": "hello world"},
            "engagement_metrics": {"views": 100, "likes": 10, "replies": 2, "retweets": 3},
        },
        "text",
    )
    assert post.title == "hello world"
    assert post.views == 100
    assert post.comments == 2
    assert post.shares == 3


class FakeSocialCrawlClient:
    """Replays one JSON response per (path, call-order) and records every call.

    Supports both GET (single-account endpoints) and POST (Prism bulk).
    """

    def __init__(self, responses_by_path: Dict[str, List[Dict[str, Any]]]):
        self.responses_by_path = responses_by_path
        self.calls: List[Dict[str, Any]] = []
        self._next_index: Dict[str, int] = {}

    def _respond(self, path: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"path": path, **extra})
        responses = self.responses_by_path.get(path, [{"success": True, "data": {}, "credits_used": 1}])
        idx = self._next_index.get(path, 0)
        response = responses[min(idx, len(responses) - 1)]
        self._next_index[path] = idx + 1
        return response

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None):
        path = url.split("/v1", 1)[-1]
        return self._respond(path, {"params": params, "headers": headers})

    def post_json(self, url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None):
        path = url.split("/v1", 1)[-1]
        return self._respond(path, {"payload": payload, "headers": headers})


# facebook/linkedin are url-keyed and stay on the single-account path -- used
# here to exercise fetch_profile() directly (instagram/tiktok/threads/x now
# go through the Prism bulk path instead, covered separately below).


def test_fetch_sends_the_api_key_header_and_url_param_for_url_keyed_platforms():
    client = FakeSocialCrawlClient(
        {
            "/facebook/profile": [{"success": True, "data": {"author": {"followers": 500}}, "credits_used": 1}],
        }
    )
    config = Config(socialcrawl_api_key="secret-key")
    metrics = SocialCrawlExtractor(config, client=client).fetch(profiles("facebook", 1), "facebook")
    assert metrics[0].followers == 500
    profile_call = next(c for c in client.calls if c["path"] == "/facebook/profile")
    assert profile_call["headers"] == {"x-api-key": "secret-key"}
    assert profile_call["params"] == {"url": "https://facebook.com/handle0"}


def test_fetch_counts_credits_used_across_every_call():
    client = FakeSocialCrawlClient(
        {
            "/facebook/profile": [{"success": True, "data": {"author": {"followers": 10}}, "credits_used": 1}],
            "/facebook/profile/posts": [{"success": True, "data": {"items": []}, "credits_used": 1}],
        }
    )
    extractor = SocialCrawlExtractor(Config(socialcrawl_api_key="k"), client=client)
    extractor.fetch(profiles("facebook", 2), "facebook")
    assert extractor.credits_used == 4  # 2 accounts * (1 profile + 1 posts)


def test_private_account_is_flagged_not_dropped():
    client = FakeSocialCrawlClient(
        {"/facebook/profile": [{"success": True, "data": {"author": {"private": True}}, "credits_used": 1}]}
    )
    metrics = SocialCrawlExtractor(Config(socialcrawl_api_key="k"), client=client).fetch(profiles("facebook", 1), "facebook")
    assert metrics[0].resolution_status == RESOLUTION_PRIVATE


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FlakyThenNotFoundClient:
    """Raises a 404 HTTPError on the profile call — exercises the not-found path."""

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/profile"):
            raise requests.HTTPError(response=_Response(404))
        return {"success": True, "data": {"items": []}, "credits_used": 0}


def test_profile_404_is_marked_not_found_not_error():
    metrics = SocialCrawlExtractor(Config(socialcrawl_api_key="k"), client=FlakyThenNotFoundClient()).fetch(
        profiles("facebook", 1), "facebook"
    )
    assert metrics[0].resolution_status == RESOLUTION_NOT_FOUND


# --- Prism bulk profiles (instagram/tiktok/threads/x) ---


def _prism_row(index: int, platform: str, handle: str, status: str = "ok", followers: int = 0, **author_extra):
    """Builds one /prism/profiles result row in the *real* shape, confirmed
    live 2026-10-05 against a real key (docs/socialcrawl-fields.md) -- it's
    data.results[], each {index, target, status, data: {author: {...}}},
    not the flat data:[{author:{...}}] list this was first built against."""
    row = {"index": index, "platform": platform, "target": {"platform": platform, "handle": handle}, "status": status}
    if status == "ok":
        row["data"] = {"author": {"followers": followers, **author_extra}}
    else:
        row["error"] = {"type": status.upper()}
    return row


def _prism_response(rows, credits_used=None):
    return {
        "success": True,
        "data": {"results": rows},
        "credits_used": credits_used if credits_used is not None else len(rows),
    }


def test_bulk_profiles_sends_one_post_call_for_up_to_fifty_accounts():
    client = FakeSocialCrawlClient(
        {
            "/prism/profiles": [
                _prism_response(
                    [
                        _prism_row(0, "instagram", "handle0", followers=100),
                        _prism_row(1, "instagram", "handle1", followers=200),
                    ]
                )
            ],
            "/instagram/profile/posts": [{"success": True, "data": {"items": []}, "credits_used": 0}],
            "/instagram/profile/reels": [{"success": True, "data": {"items": []}, "credits_used": 0}],
        }
    )
    config = Config(socialcrawl_api_key="k", socialcrawl_use_bulk_profiles=True)
    metrics = SocialCrawlExtractor(config, client=client).fetch(profiles("instagram", 2), "instagram")
    assert [m.followers for m in metrics] == [100, 200]
    bulk_calls = [c for c in client.calls if c["path"] == "/prism/profiles"]
    assert len(bulk_calls) == 1
    assert bulk_calls[0]["payload"] == {"items": [{"platform": "instagram", "handle": "handle0"}, {"platform": "instagram", "handle": "handle1"}]}


def test_bulk_profiles_splits_into_batches_of_fifty():
    responses = {
        "/prism/profiles": [
            _prism_response([_prism_row(i, "tiktok", f"handle{i}", followers=i) for i in range(50)]),
            _prism_response([_prism_row(0, "tiktok", "handle50", followers=999)]),
        ],
        "/tiktok/profile/videos": [{"success": True, "data": {"items": []}, "credits_used": 0}],
    }
    client = FakeSocialCrawlClient(responses)
    config = Config(socialcrawl_api_key="k", socialcrawl_use_bulk_profiles=True)
    metrics = SocialCrawlExtractor(config, client=client).fetch(profiles("tiktok", 51), "tiktok")
    assert len(metrics) == 51
    assert metrics[-1].followers == 999
    assert len([c for c in client.calls if c["path"] == "/prism/profiles"]) == 2


def test_bulk_profiles_uses_x_as_the_prism_platform_name():
    client = FakeSocialCrawlClient({"/prism/profiles": [_prism_response([_prism_row(0, "x", "handle0", followers=1)])]})
    config = Config(socialcrawl_api_key="k", socialcrawl_use_bulk_profiles=True)
    SocialCrawlExtractor(config, client=client).fetch(profiles("x", 1), "x")
    bulk_call = next(c for c in client.calls if c["path"] == "/prism/profiles")
    assert bulk_call["payload"]["items"][0]["platform"] == "x"


def test_bulk_profiles_error_row_does_not_drop_the_account():
    client = FakeSocialCrawlClient(
        {
            "/prism/profiles": [
                _prism_response(
                    [_prism_row(0, "threads", "handle0", status="not_found"), _prism_row(1, "threads", "handle1", followers=50)]
                )
            ]
        }
    )
    config = Config(socialcrawl_api_key="k", socialcrawl_use_bulk_profiles=True)
    metrics = SocialCrawlExtractor(config, client=client).fetch(profiles("threads", 2), "threads")
    assert len(metrics) == 2
    assert metrics[0].resolution_status != RESOLUTION_OK
    assert metrics[1].followers == 50


def test_bulk_profiles_matches_rows_by_index_not_response_order():
    # Real response includes an explicit per-row index -- trust that, not
    # array position, in case a future response is ever reordered.
    client = FakeSocialCrawlClient(
        {
            "/prism/profiles": [
                _prism_response(
                    [_prism_row(1, "x", "handle1", followers=200), _prism_row(0, "x", "handle0", followers=100)]
                )
            ]
        }
    )
    config = Config(socialcrawl_api_key="k", socialcrawl_use_bulk_profiles=True)
    metrics = SocialCrawlExtractor(config, client=client).fetch(profiles("x", 2), "x")
    by_handle = {m.handle: m.followers for m in metrics}
    assert by_handle == {"handle0": 100, "handle1": 200}


class FakeYouTubeClient:
    def __init__(self):
        self.calls: List[str] = []

    def get_json(self, url, params=None):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append(endpoint)
        params = params or {}
        if endpoint == "channels" and "forHandle" in params:
            return {"items": [{"id": "UC" + "a" * 22}]}
        if endpoint == "channels":
            return {
                "items": [
                    {
                        "id": cid,
                        "snippet": {"publishedAt": "2015-01-01T00:00:00Z"},
                        "statistics": {"subscriberCount": "1000", "viewCount": "50000", "videoCount": "42"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
                    }
                    for cid in params["id"].split(",")
                ]
            }
        if endpoint == "playlistItems":
            return {"items": [{"contentDetails": {"videoId": "vid1"}}, {"contentDetails": {"videoId": "vid2"}}]}
        if endpoint == "videos":
            return {
                "items": [
                    {"id": vid, "statistics": {"viewCount": "1000", "likeCount": "100", "commentCount": "10"}}
                    for vid in params["id"].split(",")
                ]
            }
        return {}


def test_youtube_batches_channels_fifty_per_call():
    config = Config(youtube_api_key="test")
    client = FakeYouTubeClient()
    extractor = YouTubeExtractor(config, client=client)
    ids = ["UC" + str(i).rjust(22, "0") for i in range(70)]
    extractor.fetch_channels(ids)
    assert client.calls.count("channels") == 2


def test_youtube_fetch_builds_metrics_and_caches_the_channel_id():
    config = Config(youtube_api_key="test")
    profile = CreatorProfile("Mr Beast", "youtube", "@mrbeast", "https://youtube.com/@mrbeast")
    metrics = YouTubeExtractor(config, client=FakeYouTubeClient()).fetch([profile])[0]

    assert profile.platform_id.startswith("UC")  # cached so handles can change freely
    assert metrics.followers == 1000
    assert metrics.total_views == 50000
    assert metrics.post_count == 42
    assert metrics.avg_views == 1000.0
    assert metrics.avg_likes == 100.0
    assert metrics.resolution_status == RESOLUTION_OK


def test_youtube_channel_id_in_profile_skips_handle_resolution():
    config = Config(youtube_api_key="test")
    client = FakeYouTubeClient()
    profile = CreatorProfile(
        "Mr Beast", "youtube", "UC" + "a" * 22, "https://youtube.com/channel/" + "UC" + "a" * 22,
        platform_id="UC" + "a" * 22,
    )
    YouTubeExtractor(config, client=client).fetch([profile])
    # Only channels.list, playlistItems, videos.list — no handle lookup.
    assert client.calls == ["channels", "playlistItems", "videos"]
