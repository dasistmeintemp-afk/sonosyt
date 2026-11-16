"""Lyrics for the track that is playing."""

from __future__ import annotations

import threading

from ..domain.lyrics import answer, best_match, searches, split_title
from ..domain.track import Track
from .ports import LyricsSource


class LyricsService:
    def __init__(self, source: LyricsSource):
        self.search = source.search  # replaceable, e.g. in tests
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, track: Track) -> dict:
        with self._lock:
            if track.id in self._cache:
                return self._cache[track.id]
        artist, song = split_title(track.title, track.uploader)
        result = None
        for params in searches(artist, song):
            try:
                result = best_match(self.search(params), track.duration)
            except Exception:  # noqa: BLE001 - offline or service down: just no lyrics
                result = None
            if result:
                break
        found = answer(result, artist, song)
        with self._lock:
            self._cache[track.id] = found
            while len(self._cache) > 300:
                self._cache.pop(next(iter(self._cache)))
        return found
