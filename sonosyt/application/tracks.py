"""Finding videos and remembering their details."""

from __future__ import annotations

import threading

from ..domain.errors import MediaError
from ..domain.playback import likely_picks
from ..domain.track import Track, watch_id
from .ports import AudioConverter, LibraryRepository, VideoCatalog


class TrackCatalog:
    """Search, related videos and suggestions, plus a memory of every video seen,
    so that playing by id needs no second trip to YouTube."""

    def __init__(self, catalog: VideoCatalog, converter: AudioConverter, library: LibraryRepository):
        self.catalog = catalog
        self.converter = converter
        self._known: dict[str, Track] = {}
        self._lock = threading.Lock()
        self.remember(library.tracks())

    def remember(self, tracks: list[Track]) -> None:
        with self._lock:
            for t in tracks:
                self._known[t.id] = t
            while len(self._known) > 2000:
                self._known.pop(next(iter(self._known)))

    def search(self, query: str, prefetch: bool = False, limit: int = 10) -> list[Track]:
        found = self.catalog.lookup(query, limit)
        self.remember(found)
        if prefetch:
            # Convert the most likely picks already, so a tap starts at once.
            self.converter.prefetch(likely_picks(found))
        return found

    def track(self, video_id: str) -> Track:
        watch_id(video_id)
        with self._lock:
            known = self._known.get(video_id)
        if known:
            return known
        found = self.search(video_id)
        if not found:
            raise MediaError(f"Video nicht gefunden: {video_id}")
        return found[0]

    def related(self, video_id: str) -> list[Track]:
        found = self.catalog.related(watch_id(video_id))
        self.remember(found)
        return found

    def suggest(self, query: str) -> list[str]:
        return self.catalog.suggest(query[:200])
