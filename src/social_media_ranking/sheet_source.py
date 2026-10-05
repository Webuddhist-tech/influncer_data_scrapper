"""Download the live creator roster sheet (section 3), same mechanism as
Desktop/ranking's ``extract.py``: a published Google Sheet's CSV export URL,
fetched fresh every run. A failed download falls back to the last good copy
on disk rather than aborting the whole run — a transient network blip
shouldn't block the week.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .storage import DataRepo

log = logging.getLogger(__name__)


def raw_sheet_path(repo: DataRepo) -> Path:
    return repo.root / "raw-sheet.csv"


def download_creator_sheet(url: str, fetch: Callable[[str], str], repo: DataRepo) -> Path:
    """Fetch the sheet CSV, cache it to ``data/raw-sheet.csv``, return that path.

    Falls back to the existing cached copy if the download fails or comes
    back empty; raises only when there's no download *and* no prior copy to
    fall back to.
    """
    dest = raw_sheet_path(repo)
    try:
        body = fetch(url)
    except Exception as exc:  # noqa: BLE001 - fall back to cache below
        log.warning("sheet download failed: %s", exc)
        body = ""

    if body.strip():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        return dest

    if dest.is_file():
        log.warning("sheet download returned nothing; using the cached copy at %s", dest)
        return dest

    raise RuntimeError(f"could not download the creator sheet from {url} and no cached copy exists at {dest}")
