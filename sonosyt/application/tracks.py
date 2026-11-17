"""Finding videos and remembering their details."""

from __future__ import annotations

import threading

from ..domain.errors import MediaError
from ..domain.music import artist_songs, by_artist, looks_like_song, music_only
from ..domain.playback import likely_picks
from ..domain.track import VIDEO_ID_RE, Track, watch_id
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

    def search(self, query: str, prefetch: bool = False, limit: int = 10, music: bool = False) -> list[Track]:
        """With ``music``, a search term finds songs only (links are taken as they are)."""
        term = query.strip()
        songs = music and not term.lower().startswith(("http://", "https://")) and not VIDEO_ID_RE.match(term)
        found = self.catalog.lookup(query, min(30, limit * 2) if songs else limit)
        if songs:
            found = music_only(found, term)[:limit]
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
        found = music_only(self.catalog.related(watch_id(video_id)))   # no mixes, podcasts ...
        self.remember(found)
        return found

    def artist(self, name: str) -> list[Track]:
        """An artist's songs, their own uploads first."""
        name = " ".join(str(name).split())[:80]
        if not name:
            raise ValueError("Künstler fehlt.")
        found = artist_songs(self.catalog.lookup(name, 30), name)
        self.remember(found)
        return found

    def newest(self, artist: str, limit: int = 4) -> list[Track]:
        """The artist's latest songs (their own uploads only)."""
        found = [t for t in self.catalog.latest(artist, 15) if looks_like_song(t) and by_artist(t, artist)]
        self.remember(found)
        return found[:limit]

    def suggest(self, query: str) -> list[str]:
        return self.catalog.suggest(query[:200])
