"""End-to-end stage behaviour against a temporary data repo."""
from __future__ import annotations

import json

import pytest

from social_media_ranking.links.extractor import extract_links, fetch_biolink_page, read_creator_sheet, scrape_biolink_page
from social_media_ranking.models import STATUS_RESOLVED, CreatorProfile, PlatformScore, RawMetrics
from social_media_ranking.pipeline import _unresolved_share, stage_score, stage_weekly_score
from social_media_ranking.publish import build_summary, write_latest
from social_media_ranking.storage import DataRepo, write_csv

LINKTREE_HTML = """
<html><body>
  <a href="https://www.instagram.com/natgeo/?utm_source=linktree">Instagram</a>
  <a href="https://www.youtube.com/@natgeo">YouTube</a>
  <a href="https://www.youtube.com/watch?v=abc123">Watch my video</a>
  <a href="https://my-merch-store.com/shop">Merch</a>
  <a href="mailto:hi@example.com">Email</a>
</body></html>
"""


LINKTREE_WITH_NEXT_DATA = """
<html><body>
  <a href="https://www.instagram.com/natgeo/">Instagram</a>
  <script id="__NEXT_DATA__" type="application/json">
  {"props": {"pageProps": {"account": {
    "profilePictureUrl": "https://ltcdn.linktr.ee/avatars/natgeo.jpg",
    "language": "en",
    "isActive": true
  }}}}
  </script>
</body></html>
"""


def test_fetch_biolink_page_pulls_avatar_and_language_from_next_data():
    links, meta = fetch_biolink_page("https://linktr.ee/natgeo", fetch=lambda _: LINKTREE_WITH_NEXT_DATA)
    assert "https://www.instagram.com/natgeo/" in links
    assert meta["avatar_url"] == "https://ltcdn.linktr.ee/avatars/natgeo.jpg"
    assert meta["language"] == "en"


def test_fetch_biolink_page_handles_missing_next_data_gracefully():
    links, meta = fetch_biolink_page("https://linktr.ee/natgeo", fetch=lambda _: LINKTREE_HTML)
    assert links
    assert meta == {"avatar_url": "", "language": ""}


def test_scrape_biolink_page_collects_outbound_links():
    urls = scrape_biolink_page("https://linktr.ee/natgeo", fetch=lambda _: LINKTREE_HTML)
    assert "https://www.instagram.com/natgeo/?utm_source=linktree" in urls
    assert not any(u.startswith("mailto:") for u in urls)


def test_extract_links_keeps_profiles_and_logs_only_real_rejects(tmp_path):
    sheet = tmp_path / "creators.csv"
    sheet.write_text("Creator Name,Linktree URL\nNat Geo,https://linktr.ee/natgeo\n", encoding="utf-8")

    profiles, unresolved = extract_links(sheet, fetch=lambda _: LINKTREE_HTML)

    assert {p.platform for p in profiles} == {"instagram", "youtube"}
    assert all(p.status == STATUS_RESOLVED for p in profiles)
    # The merch link is ignored, not logged; the video link is a real reject.
    reasons = " ".join(u["reason"] for u in unresolved)
    assert "merch" not in reasons
    assert "youtube" in reasons


NON_SOCIAL_ONLY_HTML = """
<html><body>
  <a href="https://my-merch-store.com/shop">Merch</a>
  <a href="https://wa.me/15551234567">WhatsApp</a>
</body></html>
"""


def test_extract_links_logs_a_page_with_only_non_social_links(tmp_path):
    # Real gap found running against the live roster: a creator whose page
    # fetches fine but has zero recognizable social links used to vanish
    # with no trace at all -- indistinguishable from "never processed".
    sheet = tmp_path / "creators.csv"
    sheet.write_text("Creator Name,Linktree URL\nGhost,https://linktr.ee/ghost\n", encoding="utf-8")

    profiles, unresolved = extract_links(sheet, fetch=lambda _: NON_SOCIAL_ONLY_HTML)

    assert profiles == []
    assert len(unresolved) == 1
    assert unresolved[0]["creator_name"] == "Ghost"
    assert "none were recognized" in unresolved[0]["reason"]


def test_extract_links_does_not_double_log_a_creator_with_a_real_rejection(tmp_path):
    sheet = tmp_path / "creators.csv"
    sheet.write_text("Creator Name,Linktree URL\nNat Geo,https://linktr.ee/natgeo\n", encoding="utf-8")
    # Only a rejected (not accepted, not ignored) link -- the youtube watch link.
    html = '<html><body><a href="https://www.youtube.com/watch?v=abc123">Watch</a></body></html>'

    _, unresolved = extract_links(sheet, fetch=lambda _: html)
    assert len(unresolved) == 1  # not also the generic fallback message


def test_extract_links_captures_avatar_url_from_the_linktree_page(tmp_path):
    sheet = tmp_path / "creators.csv"
    sheet.write_text("Creator Name,Linktree URL\nNat Geo,https://linktr.ee/natgeo\n", encoding="utf-8")

    profiles, _ = extract_links(sheet, fetch=lambda _: LINKTREE_WITH_NEXT_DATA)
    assert all(p.avatar_url == "https://ltcdn.linktr.ee/avatars/natgeo.jpg" for p in profiles)
    assert all(p.language == "en" for p in profiles)


def test_dead_biolink_page_is_logged_without_crashing_the_batch(tmp_path):
    sheet = tmp_path / "creators.csv"
    sheet.write_text(
        "Creator Name,Linktree URL\nDead,https://linktr.ee/dead\nNat Geo,https://linktr.ee/natgeo\n",
        encoding="utf-8",
    )

    def fetch(url: str) -> str:
        if "dead" in url:
            raise RuntimeError("404 not found")
        return LINKTREE_HTML

    profiles, unresolved = extract_links(sheet, fetch=fetch)
    assert len(profiles) == 2  # Nat Geo still processed
    assert any("page fetch failed" in u["reason"] for u in unresolved)


def test_read_creator_sheet_falls_back_to_first_two_columns(tmp_path):
    sheet = tmp_path / "creators.csv"
    sheet.write_text("Nat Geo,https://linktr.ee/natgeo\n", encoding="utf-8")
    assert read_creator_sheet(sheet) == [
        {
            "name": "Nat Geo",
            "url": "https://linktr.ee/natgeo",
            "confirmation": "",
            "language": "",
            "email": "",
            "whatsapp": "",
        }
    ]


def test_read_creator_sheet_picks_up_optional_confirmation_and_language_columns(tmp_path):
    sheet = tmp_path / "creators.csv"
    sheet.write_text(
        "Creator Name,Linktree URL,Confirmation,Language,Email,WhatsApp\n"
        "Nat Geo,https://linktr.ee/natgeo,Confirmed,en,nat@example.com,+15551234567\n",
        encoding="utf-8",
    )
    assert read_creator_sheet(sheet) == [
        {
            "name": "Nat Geo",
            "url": "https://linktr.ee/natgeo",
            "confirmation": "Confirmed",
            "language": "en",
            "email": "nat@example.com",
            "whatsapp": "+15551234567",
        }
    ]


def test_read_creator_sheet_matches_misspelled_email_and_whatsapp_headers(tmp_path):
    # Confirmed real-world typos in the live roster sheet.
    sheet = tmp_path / "creators.csv"
    sheet.write_text(
        "Full Name,LinkTree URL,Confirmation for Event,Emali,WhastApp\n"
        "Nat Geo,https://linktr.ee/natgeo,Confirmed,nat@example.com,+15551234567\n",
        encoding="utf-8",
    )
    row = read_creator_sheet(sheet)[0]
    assert row["email"] == "nat@example.com"
    assert row["whatsapp"] == "+15551234567"


def test_read_creator_sheet_fixes_the_lintr_ee_typo(tmp_path):
    sheet = tmp_path / "creators.csv"
    sheet.write_text("Creator Name,Linktree URL\nNat Geo,http://lintr.ee/natgeo\n", encoding="utf-8")
    assert read_creator_sheet(sheet)[0]["url"] == "https://linktr.ee/natgeo"


def test_read_creator_sheet_adds_a_missing_scheme(tmp_path):
    # Real failure found running against the live sheet: "linktr.ee/x" with
    # no "https://" fails outright at fetch time (requests has no base URL).
    sheet = tmp_path / "creators.csv"
    sheet.write_text(
        "Creator Name,Linktree URL\nA,linktr.ee/natgeo\nB,www.linktr.ee/natgeo2\n", encoding="utf-8"
    )
    rows = read_creator_sheet(sheet)
    assert rows[0]["url"] == "https://linktr.ee/natgeo"
    assert rows[1]["url"] == "https://linktr.ee/natgeo2"


def test_read_creator_sheet_fixes_a_pasted_browser_tab_title(tmp_path):
    sheet = tmp_path / "creators.csv"
    sheet.write_text(
        'Creator Name,Linktree URL\nNat Geo,"natgeo Official: Instagram, Facebook | Linktree"\n',
        encoding="utf-8",
    )
    assert read_creator_sheet(sheet)[0]["url"] == "https://linktr.ee/natgeo"


def _write_raw(repo: DataRepo, platform: str, stamp: str, records):
    rows = [r.to_row() for r in records]
    write_csv(repo.raw_file(platform, stamp), rows, list(rows[0].keys()))


def test_score_stage_uses_the_previous_snapshot_for_growth(tmp_path):
    repo = DataRepo(tmp_path / "data")
    write_csv(
        repo.creator_profiles,
        [
            {"creator_name": "A", "platform": "instagram", "handle": "a", "profile_url": "https://instagram.com/a",
             "status": "resolved", "source_url": "", "reason": "", "platform_id": ""},
            {"creator_name": "B", "platform": "instagram", "handle": "b", "profile_url": "https://instagram.com/b",
             "status": "resolved", "source_url": "", "reason": "", "platform_id": ""},
        ],
    )

    def record(name, followers):
        return RawMetrics(
            creator_name=name.upper(),
            platform="instagram",
            handle=name,
            profile_url=f"https://instagram.com/{name}",
            followers=followers,
            avg_likes=50.0,
            avg_comments=5.0,
        )

    _write_raw(repo, "instagram", "2026-09-07", [record("a", 1000), record("b", 1000)])
    _write_raw(repo, "instagram", "2026-09-14", [record("a", 2000), record("b", 1010)])

    scores = {s.creator_name: s for s in stage_score(repo, stamp="2026-09-14")}
    assert scores["A"].growth == 100.0
    assert scores["B"].growth == 0.0
    assert repo.scores_file("2026-09-14").is_file()
    assert repo.scores_latest.is_file()


def test_publish_writes_summary_and_per_platform_files(tmp_path):
    repo = DataRepo(tmp_path / "data")
    scores = [
        PlatformScore("A", "instagram", "a", followers=5000, score=90.0),
        PlatformScore("B", "instagram", "b", followers=1000, score=40.0),
        PlatformScore("A", "youtube", "@a", followers=20000, score=70.0),
    ]
    write_latest(repo, scores, {"instagram": 3, "youtube": 2}, stamp="2026-09-14")

    summary = json.loads(repo.summary_json.read_text())
    assert summary["week"] == "2026-09-14"
    assert summary["platforms"]["instagram"]["resolved"] == 2
    assert summary["platforms"]["instagram"]["total"] == 3
    assert summary["ranking"][0]["creator_name"] == "A"

    instagram = json.loads(repo.platform_json("instagram").read_text())
    assert instagram["top_creator"]["creator_name"] == "A"
    assert instagram["total_followers"] == 6000
    assert instagram["unresolved"] == 1
    # Every platform gets a file, even one with no resolved creators.
    assert json.loads(repo.platform_json("threads").read_text())["top_creator"] is None


def test_summary_marks_coverage_for_partial_creators():
    summary = build_summary(
        [PlatformScore("solo", "youtube", "solo", score=80.0)],
        {"youtube": 1},
        stamp="2026-09-14",
    )
    assert summary["ranking"][0]["coverage"] == "1/7"


def _profile(name="A", platform="instagram"):
    return CreatorProfile(creator_name=name, platform=platform, handle=name.lower(), profile_url=f"https://{platform}.com/{name.lower()}")


def test_unresolved_share_counts_neither_resolved_nor_carried(tmp_path):
    repo = DataRepo(tmp_path / "data")
    rows = [
        {"week": "2026-09-07", "creator_name": "A", "platform": "instagram", "resolved": "true", "carried_over": "false"},
        {"week": "2026-09-07", "creator_name": "B", "platform": "instagram", "resolved": "false", "carried_over": "true"},
        {"week": "2026-09-07", "creator_name": "C", "platform": "instagram", "resolved": "false", "carried_over": "false"},
    ]
    write_csv(repo.history_file("2026-09-07"), rows, list(rows[0].keys()))
    assert _unresolved_share(repo, "2026-09-07") == pytest.approx(1 / 3)


def test_stage_weekly_score_refuses_to_overwrite_a_past_week_without_rebuild(tmp_path):
    repo = DataRepo(tmp_path / "data")
    past_week = "2020-01-06"  # long past, won't equal week_label() today
    write_csv(repo.history_file(past_week), [{"week": past_week, "creator_name": "A", "platform": "instagram", "total_score": ""}], ["week", "creator_name", "platform", "total_score"])

    with pytest.raises(RuntimeError):
        stage_weekly_score(repo, [_profile()], past_week, rebuild=False)

    # --rebuild explicitly allows it.
    stage_weekly_score(repo, [_profile()], past_week, rebuild=True)
