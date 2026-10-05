"""Social Media Ranking CLI — one subcommand per pipeline stage."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from .config import PLATFORMS, Config
from .pipeline import (
    load_profiles,
    run_weekly,
    stage_export_public,
    stage_extract_links,
    stage_extract_social,
    stage_publish,
    stage_score,
    stage_validate_links,
    stage_weekly_score,
)
from .storage import DataRepo, week_label
from .weekly_score import score_week


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="social-media-ranking",
        description="Social Media Ranking pipeline",
    )
    parser.add_argument("--data-root", default="data", help="Path to the data directory (default: data)")
    parser.add_argument("--repo-root", default=".", help="Git repo root used by the publish step")
    parser.add_argument("--date", dest="stamp", default=None, help="Override the YYYY-MM-DD snapshot stamp")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract-links", help="Read the Excel sheet and scrape each bio-link page")
    extract.add_argument(
        "--input", default=None, help="creators.xlsx (or .csv); omit to use CREATOR_SHEET_CSV_URL instead"
    )
    extract.add_argument("--output", default=None, help="Override creator_profiles.csv path")
    extract.add_argument("--refresh", action="store_true", help="Bypass the 14-day page cache and re-fetch everyone")

    validate = sub.add_parser("validate-links", help="Re-run URL pattern checks on creator_profiles.csv")
    validate.add_argument("--input", default=None, help="Override creator_profiles.csv path")
    validate.add_argument("--offline", action="store_true", help="Skip redirect resolution for shortened URLs")

    social = sub.add_parser("extract-social", help="Fetch platform metrics via SocialCrawl and the YouTube API")
    social.add_argument("--platform", default="all", choices=("all", *PLATFORMS))
    social.add_argument("--input", default=None, help="Override creator_profiles.csv path")
    social.add_argument("--output", default=None, help="Override data/raw/ path")
    social.add_argument("--dry-run", action="store_true", help="Report the call plan without spending credits")
    social.add_argument("--only", default=None, help="Limit to one creator, by creator_name or slug")

    score = sub.add_parser("score", help="Compute weekly scores from the raw snapshots")
    score.add_argument("--input", default=None, help="Override data/raw/ path")
    score.add_argument("--output", default=None, help="Override data/scores/ path")

    weekly_score = sub.add_parser("weekly-score", help="Phase 6 module scoring + data/history/{week}.csv (section 6)")
    weekly_score.add_argument("--week", default=None, help="Override the computed week label (YYYY-MM-DD)")
    weekly_score.add_argument("--rebuild", action="store_true", help="Allow overwriting a past week's history")

    export = sub.add_parser("export-public", help="Build export/public/ from this week's scores (section 7)")
    export.add_argument("--week", default=None, help="Override the computed week label (YYYY-MM-DD)")

    publish = sub.add_parser("publish", help="Write latest/*.json and commit to the data repo")
    publish.add_argument("--input", default=None, help="Override data/scores/latest.csv path")
    publish.add_argument("--repo", default=None, help="<org>/<data-repo>, informational only")
    publish.add_argument("--no-push", action="store_true", help="Commit but do not push")

    weekly = sub.add_parser("run-weekly", help="Chain every stage — the cron entry point")
    weekly.add_argument("--input", default=None, help="creators.xlsx; omit to reuse creator_profiles.csv")
    weekly.add_argument("--week", default=None, help="Override the computed week label (YYYY-MM-DD)")
    weekly.add_argument("--only", default=None, help="Limit extract-social to one creator, by creator_name or slug")
    weekly.add_argument("--rebuild", action="store_true", help="Allow overwriting a past week's history")
    weekly.add_argument("--refresh", action="store_true", help="Bypass the 14-day page cache in extract-links")
    weekly.add_argument("--no-push", action="store_true")

    return parser


def _repo_for(args: argparse.Namespace) -> DataRepo:
    """Honor per-stage path overrides by deriving the data root from them."""
    root = Path(args.data_root)
    override = getattr(args, "output", None) or getattr(args, "input", None)
    if override and args.command in {"score", "extract-social"}:
        candidate = Path(override)
        if candidate.name in {"raw", "scores"}:
            root = candidate.parent
    return DataRepo(root)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    repo = _repo_for(args)
    config = Config.from_env()
    repo_root = Path(args.repo_root)

    if args.command == "extract-links":
        if args.output:
            repo = DataRepo(Path(args.output).parent)
        if not args.input and not config.creator_sheet_csv_url:
            print("error: --input is required unless CREATOR_SHEET_CSV_URL is set", file=sys.stderr)
            return 2
        stage_extract_links(
            Path(args.input) if args.input else None,
            repo,
            refresh=args.refresh,
            sheet_url=config.creator_sheet_csv_url,
        )
    elif args.command == "validate-links":
        if args.input:
            repo = DataRepo(Path(args.input).parent)
        stage_validate_links(repo, client=None if not args.offline else _NoRedirectClient())
    elif args.command == "extract-social":
        if args.input:
            repo = DataRepo(Path(args.input).parent)
        stage_extract_social(repo, config, platform=args.platform, stamp=args.stamp, dry_run=args.dry_run, only=args.only)
    elif args.command == "score":
        stage_score(repo, stamp=args.stamp)
    elif args.command == "weekly-score":
        stamp = args.week or args.stamp or week_label()
        stage_weekly_score(repo, load_profiles(repo.creator_profiles), stamp, rebuild=args.rebuild)
    elif args.command == "export-public":
        stamp = args.week or args.stamp or week_label()
        all_profiles = load_profiles(repo.creator_profiles)
        scores = score_week(repo, all_profiles, stamp)
        stage_export_public(repo, all_profiles, scores, stamp)
    elif args.command == "publish":
        if args.input:
            repo = DataRepo(Path(args.input).parent.parent)
        stage_publish(repo, repo_root, stamp=args.stamp, push=not args.no_push)
    elif args.command == "run-weekly":
        published = run_weekly(
            Path(args.input) if args.input else None,
            repo,
            config,
            repo_root,
            push=not args.no_push,
            stamp=args.week or args.stamp,
            only=args.only,
            rebuild=args.rebuild,
            refresh=args.refresh,
        )
        return 0 if published else 1
    return 0


class _NoRedirectClient:
    """Stand-in for --offline: never touches the network."""

    @staticmethod
    def resolve_redirect(url: str) -> str:
        return url


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
