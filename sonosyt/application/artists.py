"""Artists: their page, following them, and their newest songs on the start screen."""

from __future__ import annotations

import logging
import threading
import time

from ..domain.errors import MediaError
from .ports import LibraryRepository
from .tracks import TrackCatalog

log = logging.getLogger(__name__)
FRESH = 6 * 3600.0   # new songs do not appear by the minute
PER_ARTIST = 3


class ArtistService:
    """New songs of followed artists are looked up in the background, one artist
    after another, so the start screen never waits for YouTube."""

    def __init__(self, tracks: TrackCatalog, library: LibraryRepository, clock=time.time, background: bool = True):
        self.tracks = tracks
        self.library = library
        self.clock = clock
        self.background = background
        self._newest: dict[str, tuple[float, list[dict]]] = {}   # artist -> (when, songs)
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def page(self, name: str) -> dict:
        songs = self.tracks.artist(name)
        followed = any(a.lower() == name.strip().lower() for a in self.library.artists())
        return {"name": name.strip(), "followed": followed, "tracks": [t.to_dict() for t in songs]}

    def follow(self, name: str, on: bool) -> dict:
        self.library.set_follow(name, bool(on))
        if on:
            with self._lock:
                self._newest.pop(name.strip(), None)
        return {"artists": self.library.artists()}

    def _stale(self, artist: str) -> bool:
        known = self._newest.get(artist)
        return not known or self.clock() - known[0] > FRESH

    def new_songs(self) -> dict:
        artists = self.library.artists()
        with self._lock:
            missing = [a for a in artists if self._stale(a)]
        if missing:
            if self.background:
                self._start(missing)
            else:
                self.refresh(missing)
        with self._lock:
            lists = [self._newest[a][1] for a in artists if a in self._newest]
            pending = [a for a in artists if a not in self._newest]
        # newest of each artist first, then the second of each ...: no artist crowds out the others
        items, seen = [], set()
        for i in range(PER_ARTIST):
            for songs in lists:
                if i < len(songs) and songs[i]["id"] not in seen:
                    seen.add(songs[i]["id"])
                    items.append(songs[i])
        return {"artists": artists, "items": items, "pending": pending}

    def _start(self, artists: list[str]) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self.refresh, args=(artists,), name="artists", daemon=True)
            self._worker.start()

    def refresh(self, artists: list[str]) -> None:
        for artist in artists:
            try:
                songs = [{**t.to_dict(), "artist": artist} for t in self.tracks.newest(artist, PER_ARTIST)]
            except MediaError as exc:
                log.info("artists: %s: %s", artist, exc)
                songs = []
            with self._lock:
                self._newest[artist] = (self.clock(), songs)
