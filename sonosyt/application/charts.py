"""The current hits on the start screen, found on YouTube."""

from __future__ import annotations

import logging
import threading
import time

from ..domain.charts import best_match, chart_query, chart_title
from ..domain.errors import MediaError
from .ports import ChartSource
from .tracks import TrackCatalog

log = logging.getLogger(__name__)
FRESH = 6 * 3600.0   # hit lists change once a day
SIZE = 40            # songs fetched
FIND = 30            # songs looked up on YouTube, best first


class ChartService:
    """Fetches the hit list now and then and looks its songs up on YouTube in the
    background, one after another, so the start screen never waits for it."""

    def __init__(self, source: ChartSource, tracks: TrackCatalog, country: str = "de",
                 clock=time.time, background: bool = True):
        self.source = source
        self.tracks = tracks
        self.country = (country or "de").strip().lower()
        self.clock = clock
        self.background = background
        self._songs: list[dict] = []
        self._fetched: float | None = None   # never
        self._error: str | None = None
        self._found: dict[str, dict | None] = {}   # query -> track (None: nothing suitable)
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def get(self) -> dict:
        if self._fetched is None or self.clock() - self._fetched > FRESH:
            self._refresh()
        if self.background:
            self._start_finding()
        else:
            self.find_all()
        with self._lock:
            items = []
            for rank, song in enumerate(self._songs, 1):
                query = chart_query(song["artist"], song["title"])
                items.append({**song, "rank": rank, "query": query, "track": self._found.get(query),
                              "pending": rank <= FIND and query not in self._found})
            return {"title": chart_title(self.country), "country": self.country, "items": items,
                    "error": self._error if not items else None}

    def _refresh(self) -> None:
        try:
            songs = self.source.top_songs(self.country, SIZE)
        except (MediaError, ValueError) as exc:
            log.warning("charts: %s", exc)
            with self._lock:
                self._error = str(exc)
                self._fetched = self.clock() - FRESH + 600   # try again in ten minutes
            return
        with self._lock:
            self._songs, self._error, self._fetched = songs, None, self.clock()

    def _start_finding(self) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            if all(chart_query(s["artist"], s["title"]) in self._found for s in self._songs[:FIND]):
                return
            self._worker = threading.Thread(target=self.find_all, name="charts", daemon=True)
            self._worker.start()

    def find_all(self) -> None:
        with self._lock:
            songs = list(self._songs[:FIND])
        for song in songs:
            query = chart_query(song["artist"], song["title"])
            with self._lock:
                if query in self._found:
                    continue
            try:
                hit = best_match(self.tracks.search(query, limit=4), song["artist"], song["title"])
            except MediaError as exc:
                log.info("charts: %s not found: %s", query, exc)
                continue   # asked again on the next visit
            with self._lock:
                self._found[query] = hit.to_dict() if hit else None
