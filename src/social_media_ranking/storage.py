"""File layout for the data repo (section 2) plus CSV/JSON helpers."""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


def today_stamp() -> str:
    return date.today().isoformat()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def week_label(today: Optional[date] = None) -> str:
    """The Monday (<= today) that labels the 7-day window ending at it (section 5)."""
    today = today or date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


class DataRepo:
    """Resolves the documented paths under a data repo root."""

    def __init__(self, root: Path | str = "data"):
        self.root = Path(root)

    @property
    def creator_profiles(self) -> Path:
        return self.root / "creator_profiles.csv"

    @property
    def overrides_json(self) -> Path:
        """Manual fixes only — never hand-edit a generated file instead (section 2)."""
        return self.root / "overrides.json"

    def raw_dir(self, platform: str) -> Path:
        return self.root / "raw" / platform

    def raw_file(self, platform: str, stamp: Optional[str] = None) -> Path:
        return self.raw_dir(platform) / f"{stamp or today_stamp()}.csv"

    def scores_file(self, stamp: Optional[str] = None) -> Path:
        return self.root / "scores" / f"{stamp or today_stamp()}.csv"

    @property
    def scores_latest(self) -> Path:
        return self.root / "scores" / "latest.csv"

    @property
    def latest_dir(self) -> Path:
        return self.root / "latest"

    @property
    def summary_json(self) -> Path:
        return self.latest_dir / "summary.json"

    def platform_json(self, platform: str) -> Path:
        return self.latest_dir / f"{platform}.json"

    def unresolved_log(self, stamp: Optional[str] = None) -> Path:
        return self.root.parent / "logs" / "unresolved" / f"{stamp or today_stamp()}.csv"

    @property
    def history_dir(self) -> Path:
        return self.root / "history"

    def history_file(self, week: str) -> Path:
        return self.history_dir / f"{week}.csv"

    def history_weeks(self) -> List[str]:
        """Every week we have history for, oldest first."""
        if not self.history_dir.is_dir():
            return []
        return sorted(p.stem for p in self.history_dir.glob("*.csv"))

    def raw_snapshots(self, platform: str) -> List[Path]:
        """Dated raw files for a platform, oldest first."""
        directory = self.raw_dir(platform)
        if not directory.is_dir():
            return []
        return sorted(p for p in directory.glob("*.csv") if p.stem != "latest")

    def previous_raw_file(self, platform: str, stamp: str) -> Optional[Path]:
        """The most recent snapshot strictly before ``stamp`` — the growth baseline."""
        earlier = [p for p in self.raw_snapshots(platform) if p.stem < stamp]
        return earlier[-1] if earlier else None


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(fieldnames or (rows[0].keys() if rows else []))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not Path(path).is_file():
        return []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def append_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.is_file()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, payload: Any) -> Path:
    """2-space indent, sorted keys, UTF-8 (section 7)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
