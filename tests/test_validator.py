"""Section 3 test cases — one fixture list per platform, plus the 8 named cases."""
from __future__ import annotations

import pytest

from social_media_ranking.links.validator import (
    ACCEPTED,
    IGNORED,
    REJECTED,
    LinkCandidate,
    classify_platform,
    normalize_url,
    validate_candidates,
    validate_url,
)

# Case 1 — valid channel/profile URLs, one fixture list per platform.
VALID_PROFILES = [
    ("youtube", "https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA"),
    ("youtube", "https://www.youtube.com/@mrbeast"),
    ("youtube", "https://youtube.com/c/PewDiePie"),
    ("youtube", "https://www.youtube.com/user/pewdiepie"),
    ("instagram", "https://www.instagram.com/natgeo/"),
    ("instagram", "https://instagram.com/cristiano"),
    ("facebook", "https://www.facebook.com/nasa"),
    ("facebook", "https://facebook.com/profile.php?id=100044512345678"),
    ("facebook", "https://www.facebook.com/pages/Some-Page/123456789"),
    ("threads", "https://www.threads.net/@zuck"),
    ("threads", "https://threads.com/@zuck"),
    ("linkedin", "https://www.linkedin.com/in/williamhgates"),
    ("linkedin", "https://uk.linkedin.com/company/microsoft"),
    ("tiktok", "https://www.tiktok.com/@khaby.lame"),
]

# Case 2 — same domain, but a content URL.
CONTENT_URLS = [
    ("youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("youtube", "https://www.youtube.com/shorts/abc123XYZ"),
    ("youtube", "https://www.youtube.com/playlist?list=PL123"),
    ("instagram", "https://www.instagram.com/p/CxYzAbCdEfG/"),
    ("instagram", "https://www.instagram.com/reel/CxYzAbCdEfG/"),
    ("instagram", "https://www.instagram.com/stories/natgeo/123456789/"),
    ("facebook", "https://www.facebook.com/nasa/posts/123456789"),
    ("facebook", "https://www.facebook.com/nasa/videos/987654321"),
    ("facebook", "https://www.facebook.com/photo/?fbid=123"),
    ("facebook", "https://www.facebook.com/watch/?v=123456"),
    ("threads", "https://www.threads.net/@zuck/post/C1a2b3c4"),
    ("linkedin", "https://www.linkedin.com/posts/williamhgates_activity-123"),
    ("linkedin", "https://www.linkedin.com/pulse/some-article-name"),
    ("tiktok", "https://www.tiktok.com/@khaby.lame/video/7123456789"),
]


@pytest.mark.parametrize("platform,url", VALID_PROFILES)
def test_case1_valid_profile_accepted(platform, url):
    result = validate_url(url)
    assert result.outcome == ACCEPTED, result.reason
    assert result.platform == platform
    assert result.handle


@pytest.mark.parametrize("platform,url", CONTENT_URLS)
def test_case2_content_url_rejected(platform, url):
    result = validate_url(url)
    assert result.outcome == REJECTED
    assert result.platform == platform


def test_case3_tracking_params_stripped_then_accepted():
    result = validate_url("https://www.instagram.com/natgeo/?utm_source=linktree&igshid=abc")
    assert result.outcome == ACCEPTED
    assert result.normalized_url == "https://instagram.com/natgeo"


def test_case3_tracking_params_do_not_rescue_a_content_url():
    result = validate_url("https://www.instagram.com/p/Cxyz/?utm_source=linktree")
    assert result.outcome == REJECTED


def test_case4_shortener_resolved_then_reclassified():
    redirects = {
        "https://youtu.be/dQw4w9WgXcQ": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://bit.ly/natgeo": "https://www.instagram.com/natgeo/",
    }

    def resolver(url: str) -> str:
        return redirects.get(url, url)

    assert validate_url("https://youtu.be/dQw4w9WgXcQ", resolver=resolver).outcome == REJECTED

    accepted = validate_url("https://bit.ly/natgeo", resolver=resolver)
    assert accepted.outcome == ACCEPTED
    assert accepted.platform == "instagram"
    assert accepted.handle == "natgeo"


def test_case4_shortener_without_resolver_is_logged_not_accepted():
    result = validate_url("https://bit.ly/natgeo")
    assert result.outcome == REJECTED
    assert "shorten" in result.reason


def test_case5_trailing_slash_variants_normalize_and_dedupe():
    a = validate_url("https://instagram.com/natgeo", "Nat Geo")
    b = validate_url("https://instagram.com/natgeo/", "Nat Geo")
    assert a.normalized_url == b.normalized_url

    deduped = validate_candidates(
        [
            LinkCandidate("Nat Geo", "https://instagram.com/natgeo"),
            LinkCandidate("Nat Geo", "https://www.instagram.com/natgeo/"),
            LinkCandidate("Nat Geo", "https://instagram.com/natgeo/?utm_source=x"),
        ]
    )
    assert len(deduped) == 1


def test_case6_case_insensitive_domain_and_handle():
    result = validate_url("HTTPS://Instagram.COM/NatGeo/")
    assert result.outcome == ACCEPTED
    assert result.platform == "instagram"
    assert result.normalized_url == "https://instagram.com/natgeo"


def test_case7_non_social_link_is_ignored_not_an_error():
    result = validate_url("https://my-merch-store.com/shop")
    assert result.outcome == IGNORED
    assert result.platform == ""


def test_case8_empty_url_does_not_crash():
    assert validate_url("").outcome == REJECTED
    assert validate_url("   ").outcome == REJECTED


def test_classify_platform_handles_regional_subdomains():
    assert classify_platform("https://de-de.facebook.com/nasa") == "facebook"
    assert classify_platform("https://m.youtube.com/@mrbeast") == "youtube"
    assert classify_platform("https://example.org") == ""


def test_normalize_url_adds_scheme_and_collapses_slashes():
    assert normalize_url("instagram.com//natgeo//") == "https://instagram.com/natgeo"


def test_youtube_channel_id_must_be_well_formed():
    assert validate_url("https://youtube.com/channel/notachannelid").outcome == REJECTED


def test_instagram_reserved_word_is_not_a_username():
    assert validate_url("https://instagram.com/explore").outcome == REJECTED


def test_linkedin_requires_in_or_company_prefix():
    assert validate_url("https://linkedin.com/williamhgates").outcome == REJECTED


def test_tiktok_requires_at_prefix():
    assert validate_url("https://tiktok.com/khaby.lame").outcome == REJECTED


def test_handles_are_canonicalized_per_platform():
    assert validate_url("https://www.tiktok.com/@Khaby.Lame").normalized_url == "https://tiktok.com/@khaby.lame"
    assert validate_url("https://www.linkedin.com/in/WilliamHGates").normalized_url == (
        "https://linkedin.com/in/williamhgates"
    )
