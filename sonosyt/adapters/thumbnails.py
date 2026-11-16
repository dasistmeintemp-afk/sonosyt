"""Cover images kept on disk, so each is fetched from YouTube only once. Implements Thumbnails."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..domain.track import watch_id

LIMIT = 3000  # files kept (~15 kB each)


class DiskThumbnailCache:
    def __init__(self, folder: str | os.PathLike, fetch: Callable[[str], bytes], limit: int = LIMIT):
        self.folder = Path(folder)
        self.fetch = fetch  # raises MediaError when there is no cover
        self.limit = limit

    def get(self, video_id: str) -> bytes:
        path = self.folder / f"{watch_id(video_id)}.jpg"
        try:
            return path.read_bytes()
        except OSError:
            pass
        data = self.fetch(video_id)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            files = sorted(self.folder.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
            for old in files[: max(0, len(files) - self.limit)]:
                old.unlink(missing_ok=True)
        except OSError:
            pass  # serving still works without the cache
        return data
