"""Build the dashboard JSON rollups and push them to the data repo (section 10)."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .config import PLATFORMS
from .models import CreatorScore, PlatformScore
from .scoring import combine_scores
from .storage import DataRepo, today_stamp, utc_now, write_json

log = logging.getLogger(__name__)


def build_platform_payload(platform: str, scores: Sequence[PlatformScore], total_profiles: int) -> Dict:
    """Per-platform leaderboard: top creator, totals, resolved/total count."""
    ranked = sorted(scores, key=lambda s: s.score, reverse=True)
    followers = [s.followers or 0 for s in ranked]
    return {
        "platform": platform,
        "generated_at": utc_now(),
        "resolved": len(ranked),
        "total": total_profiles,
        "unresolved": max(total_profiles - len(ranked), 0),
        "total_followers": sum(followers),
        "top_creator": (
            {
                "creator_name": ranked[0].creator_name,
                "handle": ranked[0].handle,
                "followers": ranked[0].followers,
                "score": ranked[0].score,
            }
            if ranked
            else None
        ),
        "leaderboard": [
            {
                "rank": idx + 1,
                "creator_name": s.creator_name,
                "handle": s.handle,
                "followers": s.followers,
                "reach": s.reach,
                "engagement": s.engagement,
                "growth": s.growth,
                "score": s.score,
                "carried_over": s.carried_over,
            }
            for idx, s in enumerate(ranked)
        ],
    }


def build_summary(
    platform_scores: Sequence[PlatformScore],
    profile_counts: Dict[str, int],
    combined: Optional[List[CreatorScore]] = None,
    stamp: Optional[str] = None,
) -> Dict:
    """The single file the dashboard fetches."""
    combined = combined if combined is not None else combine_scores(platform_scores)
    by_platform: Dict[str, List[PlatformScore]] = {}
    for entry in platform_scores:
        by_platform.setdefault(entry.platform, []).append(entry)

    return {
        "week": stamp or today_stamp(),
        "generated_at": utc_now(),
        "creators_ranked": len(combined),
        "platforms": {
            platform: {
                "resolved": len(by_platform.get(platform, [])),
                "total": profile_counts.get(platform, 0),
                "total_followers": sum(s.followers or 0 for s in by_platform.get(platform, [])),
                "top_creator": (
                    max(by_platform[platform], key=lambda s: s.score).creator_name
                    if by_platform.get(platform)
                    else None
                ),
            }
            for platform in PLATFORMS
        },
        "ranking": [
            {
                "rank": idx + 1,
                "creator_name": c.creator_name,
                "total_score": c.total_score,
                "coverage": c.coverage,
                "resolved_platforms": c.resolved_platforms,
                "platform_scores": c.platform_scores,
            }
            for idx, c in enumerate(combined)
        ],
    }


def write_latest(
    repo: DataRepo,
    platform_scores: Sequence[PlatformScore],
    profile_counts: Dict[str, int],
    stamp: Optional[str] = None,
) -> List[Path]:
    written = [write_json(repo.summary_json, build_summary(platform_scores, profile_counts, stamp=stamp))]
    by_platform: Dict[str, List[PlatformScore]] = {p: [] for p in PLATFORMS}
    for entry in platform_scores:
        by_platform.setdefault(entry.platform, []).append(entry)
    for platform, scores in by_platform.items():
        payload = build_platform_payload(platform, scores, profile_counts.get(platform, 0))
        written.append(write_json(repo.platform_json(platform), payload))
    return written


def git_publish(repo_root: Path | str, paths: Iterable[Path], message: str, push: bool = True) -> bool:
    """Commit and push the changed data files. Returns False when nothing changed."""
    repo_root = Path(repo_root)
    relative = [str(Path(p).resolve().relative_to(repo_root.resolve())) for p in paths]

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(args, cwd=repo_root, capture_output=True, text=True, check=False)

    run("git", "add", *relative)
    status = run("git", "status", "--porcelain", "--", *relative)
    if not status.stdout.strip():
        log.info("no data changes to publish")
        return False

    commit = run("git", "commit", "-m", message)
    if commit.returncode != 0:
        log.error("git commit failed: %s", commit.stderr.strip())
        return False
    if push:
        pushed = run("git", "push")
        if pushed.returncode != 0:
            log.error("git push failed: %s", pushed.stderr.strip())
            return False
    return True
