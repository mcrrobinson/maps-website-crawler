"""Fetch the authors' released rating-based weights (~96 MB) from Google Drive."""
from __future__ import annotations

import os

DRIVE_FILE_ID = "14UYwqtCR-sV1831ZbLCXnVKrwoGHeQ1f"
DEFAULT_WEIGHTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weights", "calista_rating_based.h5")


def download_weights(dest: str = DEFAULT_WEIGHTS, force: bool = False) -> str:
    if os.path.exists(dest) and not force:
        return dest
    import gdown

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    out = gdown.download(id=DRIVE_FILE_ID, output=dest, quiet=False)
    if not out or not os.path.exists(dest):
        raise RuntimeError(f"download of Calista weights (Drive id {DRIVE_FILE_ID}) failed")
    return dest
