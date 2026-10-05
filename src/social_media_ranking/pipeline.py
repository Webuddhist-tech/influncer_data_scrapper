"""Stage functions behind each CLI subcommand.

Every stage reads and writes files, so any one of them can be re-run alone
after a partial failure.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import PLATFORMS, SOCIALCRAWL_PLATFORMS, YOUTUBE_MAX_IDS_PER_CALL, Config
from .export_public import build_export, write_export
from .extractors.socialcrawl import SocialCrawlExtractor, estimate_calls, estimate_credits
from .extractors.youtube import YouTubeExtractor
from .http import HttpClient
from .links.extractor import extract_links, revalidate_profiles, write_profiles
from .models import RESOLUTION_OK, STATUS_RESOLVED, CreatorProfile, PlatformScore, RawMetrics
from .module_scoring import CreatorWeekScore
from .overrides import apply_creator_meta
from .page_cache import CachedFetcher
from .sheet_source import download_creator_sheet
from .publish import build_summary, git_publish, write_latest
from .scoring import carry_forward, score_all
from .storage import DataRepo, append_csv, read_csv, today_stamp, utc_now, week_label, write_csv
from .weekly_score import score_week

log = logging.getLogger(__name__)

UNRESOLVED_FIELDS = ("date", "creator_name", "url", "reason", "stage")
# Section 8: refuse to publish when more than this share of accounts are
# unresolved this week -- more likely an outage or a key problem than reality.
MAX_UNRESOLVED_SHARE = 0.3


def load_profiles(path: Path) -> List[CreatorProfile]:
    return [CreatorProfile.from_row(row) for row in read_csv(path)]


def profile_counts(profiles: Sequence[CreatorProfile]) -> Dict[str, int]:
    counts = {platform: 0 for platform in PLATFORMS}
    for profile in profiles:
        if profile.platform in counts:
            counts[profile.platform] += 1
    return counts


def stage_extract_links(
    sheet: Optional[Path],
    repo: DataRepo,
    client: Optional[HttpClient] = None,
    refresh: bool = False,
    sheet_url: str = "",
) -> List[CreatorProfile]:
    client = client or HttpClient()
    if sheet is None:
        if not sheet_url:
            raise ValueError("stage_extract_links needs either a sheet path or CREATOR_SHEET_CSV_URL")
        # Live Google Sheet (section 3): download fresh every run, same CSV
        # export mechanism as Desktop/ranking's extract.py.
        sheet = download_creator_sheet(sheet_url, client.get_text, repo)
    # Every creator's page is cached (14 days), so re-running this against
    # the whole sheet every week is cheap -- a brand-new row is fetched for
    # real; everyone else is a cache hit. --refresh bypasses the cache.
    fetch = CachedFetcher(repo, client.get_text, refresh=refresh)
    profiles, unresolved = extract_links(sheet, fetch=fetch, resolver=client.resolve_redirect)
    apply_creator_meta(repo, profiles)
    write_profiles(repo, profiles)
    append_csv(repo.unresolved_log(), unresolved, UNRESOLVED_FIELDS)
    log.info("extracted %d profiles, %d unresolved links", len(profiles), len(unresolved))
    return profiles


def stage_validate_links(repo: DataRepo, client: Optional[HttpClient] = None) -> List[CreatorProfile]:
    profiles = load_profiles(repo.creator_profiles)
    resolver = (client or HttpClient()).resolve_redirect
    updated, unresolved = revalidate_profiles(profiles, resolver=resolver)
    apply_creator_meta(repo, updated)
    write_profiles(repo, updated)
    append_csv(repo.unresolved_log(), unresolved, UNRESOLVED_FIELDS)
    log.info("revalidated %d profiles, %d now invalid", len(updated), len(unresolved))
    return updated


def _due_for_refresh(repo: DataRepo, platform: str, profiles: Sequence[CreatorProfile], stamp: str) -> List[CreatorProfile]:
    """Skip anyone already scraped this week so a rerun costs no extra credits."""
    existing = read_csv(repo.raw_file(platform, stamp))
    done = {row.get("profile_url", "") for row in existing if row.get("resolution_status") == RESOLUTION_OK}
    return [p for p in profiles if p.profile_url not in done]


def _matches_only(profile: CreatorProfile, only: Optional[str]) -> bool:
    return not only or profile.creator_name == only or profile.slug == only


def stage_extract_social(
    repo: DataRepo,
    config: Config,
    platform: str = "all",
    stamp: Optional[str] = None,
    dry_run: bool = False,
    only: Optional[str] = None,
) -> Dict[str, List[RawMetrics]]:
    stamp = stamp or today_stamp()
    all_profiles = load_profiles(repo.creator_profiles)
    # merged_into (section 3): a merged creator is skipped everywhere downstream.
    profiles = [
        p for p in all_profiles if p.status == STATUS_RESOLVED and not p.merged_into and _matches_only(p, only)
    ]
    targets = PLATFORMS if platform == "all" else (platform,)
    results: Dict[str, List[RawMetrics]] = {}
    # One extractor instance for the whole run: the 600/minute rate limit and
    # credit total are per-key, not per-platform (section 4).
    social_crawl = SocialCrawlExtractor(config)

    for name in targets:
        group = [p for p in profiles if p.platform == name]
        if not group:
            continue
        pending = _due_for_refresh(repo, name, group, stamp)
        if not pending:
            log.info("%s: already fetched this week, skipping", name)
            continue

        if dry_run:
            if name == "youtube":
                # channels.list only; playlistItems/videos.list add more but scale with content, not roster size.
                calls = -(-len(pending) // YOUTUBE_MAX_IDS_PER_CALL)
                log.info("%s: %d profiles -> ~%d quota-unit calls (no SocialCrawl credits)", name, len(pending), calls)
            else:
                calls = estimate_calls(pending, name, use_bulk_profiles=config.socialcrawl_use_bulk_profiles)
                credits = estimate_credits(pending, name)
                log.info(
                    "%s: %d profiles -> ~%d calls, ~%d credits (floor -- posts beyond one page cost more)",
                    name,
                    len(pending),
                    calls,
                    credits,
                )
            continue

        if name == "youtube":
            metrics = YouTubeExtractor(config).fetch(pending, repo=repo)
        elif name in SOCIALCRAWL_PLATFORMS:
            metrics = social_crawl.fetch(pending, name, repo=repo)
        else:
            continue

        results[name] = metrics
        rows = [m.to_row() for m in metrics]
        existing = read_csv(repo.raw_file(name, stamp))
        merged = {row.get("profile_url"): row for row in existing}
        merged.update({row["profile_url"]: row for row in rows})
        write_csv(repo.raw_file(name, stamp), list(merged.values()), list(rows[0].keys()) if rows else None)

        failures = [
            {
                "date": stamp,
                "creator_name": m.creator_name,
                "url": m.profile_url,
                "reason": m.resolution_status,
                "stage": "extract-social",
            }
            for m in metrics
            if m.resolution_status != RESOLUTION_OK
        ]
        append_csv(repo.unresolved_log(stamp), failures, UNRESOLVED_FIELDS)
        log.info("%s: fetched %d profiles (%d failed)", name, len(metrics), len(failures))

    # Persist any YouTube channel IDs resolved during this run; the extractor
    # mutates the profile objects in place, so writing the full list keeps them.
    if not dry_run:
        write_profiles(repo, all_profiles)
    if not dry_run and social_crawl.credits_used:
        log.info("SocialCrawl: %d credits used this run", social_crawl.credits_used)
    return results


def _load_raw(repo: DataRepo, platform: str, path: Optional[Path]) -> List[RawMetrics]:
    if path is None or not path.is_file():
        return []
    return [RawMetrics.from_row(row) for row in read_csv(path)]


def stage_score(repo: DataRepo, stamp: Optional[str] = None) -> List[PlatformScore]:
    stamp = stamp or today_stamp()
    current: Dict[str, List[RawMetrics]] = {}
    previous: Dict[str, Dict[str, RawMetrics]] = {}

    for platform in PLATFORMS:
        records = _load_raw(repo, platform, repo.raw_file(platform, stamp))
        if not records:
            continue
        current[platform] = records
        prior_path = repo.previous_raw_file(platform, stamp)
        prior = _load_raw(repo, platform, prior_path)
        if prior:
            previous[platform] = {r.profile_url: r for r in prior}

    scores = score_all(current, previous)

    # A transient failure should not drop anyone off the leaderboard.
    last_scores = [
        PlatformScore(
            creator_name=row["creator_name"],
            platform=row["platform"],
            handle=row.get("handle", ""),
            followers=int(row["followers"]) if row.get("followers") else None,
            reach=float(row.get("reach") or 0),
            engagement=float(row.get("engagement") or 0),
            growth=float(row.get("growth") or 0),
            score=float(row.get("score") or 0),
        )
        for row in read_csv(repo.scores_latest)
    ]
    scores = carry_forward(scores, last_scores)

    rows = [s.to_row() for s in scores]
    fieldnames = list(PlatformScore("", "", "").to_row().keys())
    write_csv(repo.scores_file(stamp), rows, fieldnames)
    write_csv(repo.scores_latest, rows, fieldnames)
    log.info("scored %d creator-platform rows", len(scores))
    return scores


def stage_publish(
    repo: DataRepo,
    repo_root: Path,
    stamp: Optional[str] = None,
    push: bool = True,
) -> bool:
    stamp = stamp or today_stamp()
    scores = [
        PlatformScore(
            creator_name=row["creator_name"],
            platform=row["platform"],
            handle=row.get("handle", ""),
            followers=int(row["followers"]) if row.get("followers") else None,
            reach=float(row.get("reach") or 0),
            engagement=float(row.get("engagement") or 0),
            growth=float(row.get("growth") or 0),
            score=float(row.get("score") or 0),
            carried_over=str(row.get("carried_over", "")).lower() == "true",
        )
        for row in read_csv(repo.scores_latest)
    ]
    counts = profile_counts(load_profiles(repo.creator_profiles))
    written = write_latest(repo, scores, counts, stamp=stamp)
    written.extend([repo.scores_file(stamp), repo.scores_latest])
    summary = build_summary(scores, counts, stamp=stamp)
    log.info("published week %s: %d creators ranked", stamp, summary["creators_ranked"])
    return git_publish(repo_root, [p for p in written if Path(p).exists()], f"data: weekly refresh {stamp}", push=push)


def stage_weekly_score(
    repo: DataRepo,
    all_profiles: Sequence[CreatorProfile],
    stamp: str,
    rebuild: bool = False,
) -> Dict[str, CreatorWeekScore]:
    """Phase 6 module scoring (section 6) -- separate from the legacy
    ``stage_score`` above, kept only for the sample-check comparison."""
    if not rebuild and repo.history_file(stamp).is_file() and stamp < week_label():
        raise RuntimeError(f"week {stamp} is in the past and already has history; pass --rebuild to overwrite it")
    return score_week(repo, all_profiles, stamp)


def stage_export_public(
    repo: DataRepo,
    all_profiles: Sequence[CreatorProfile],
    scores: Dict[str, CreatorWeekScore],
    stamp: str,
) -> Path:
    export = build_export(repo, all_profiles, scores, week=stamp)
    dest = write_export(export, repo, generated_at=utc_now())
    log.info("exported %d creators to %s", len(export["summary"]["creators"]), dest)
    return dest


def _unresolved_share(repo: DataRepo, stamp: str) -> float:
    rows = read_csv(repo.history_file(stamp))
    if not rows:
        return 0.0
    unresolved = sum(1 for r in rows if r.get("resolved") != "true" and r.get("carried_over") != "true")
    return unresolved / len(rows)


def run_weekly(
    sheet: Optional[Path],
    repo: DataRepo,
    config: Config,
    repo_root: Path,
    push: bool = True,
    stamp: Optional[str] = None,
    only: Optional[str] = None,
    rebuild: bool = False,
    refresh: bool = False,
) -> bool:
    """The single entry point the cron job calls.

    Returns ``False`` (without publishing or exporting anything) when more
    than ``MAX_UNRESOLVED_SHARE`` of accounts are unresolved this week
    (section 8) -- the CLI turns that into a non-zero exit so CI fails loudly
    instead of silently publishing a broken week.
    """
    stamp = stamp or week_label()
    if sheet or config.creator_sheet_csv_url:
        # The page cache makes a full extract cheap enough to run every
        # week, not just monthly, so a brand-new creator in the sheet is
        # never more than a week away from showing up. With a live sheet
        # configured, there's no need for an explicit --input at all.
        stage_extract_links(sheet, repo, refresh=refresh, sheet_url=config.creator_sheet_csv_url)
    else:
        stage_validate_links(repo)
    stage_extract_social(repo, config, stamp=stamp, only=only)
    stage_score(repo, stamp=stamp)  # legacy score, kept for the sample-check comparison

    all_profiles = load_profiles(repo.creator_profiles)
    scores = stage_weekly_score(repo, all_profiles, stamp, rebuild=rebuild)

    unresolved_share = _unresolved_share(repo, stamp)
    if unresolved_share > MAX_UNRESOLVED_SHARE:
        log.error(
            "refusing to publish week %s: %.0f%% of accounts unresolved (>%.0f%% threshold) -- likely an outage or a key problem",
            stamp,
            unresolved_share * 100,
            MAX_UNRESOLVED_SHARE * 100,
        )
        return False

    stage_export_public(repo, all_profiles, scores, stamp)
    stage_publish(repo, repo_root, stamp=stamp, push=push)
    return True
