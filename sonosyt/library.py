"""Favourites, play history and per-room settings, kept in one JSON file."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .youtube import Track

HISTORY_LIMIT = 50


class Library:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._favorites: list[Track] = []
        self._history: list[Track] = []
        self._autoplay: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return

        def tracks(key: str) -> list[Track]:
            out = []
            for item in data.get(key) or []:
                try:
                    out.append(Track.from_dict(item))
                except (KeyError, TypeError, ValueError):
                    continue
            return out

        self._favorites = tracks("favorites")
        self._history = tracks("history")[:HISTORY_LIMIT]
        self._autoplay = {str(name) for name in data.get("autoplay") or []}

    def _save(self) -> None:
        data = {
            "favorites": [t.to_dict() for t in self._favorites],
            "history": [t.to_dict() for t in self._history],
            "autoplay": sorted(self._autoplay),
        }
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # a read-only data directory must not break playback

    # -- favourites / history ----------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "favorites": [t.to_dict() for t in self._favorites],
                "history": [t.to_dict() for t in self._history],
            }

    def tracks(self) -> list[Track]:
        with self._lock:
            return self._favorites + self._history

    def set_favorite(self, track: Track, on: bool) -> None:
        with self._lock:
            self._favorites = [t for t in self._favorites if t.id != track.id]
            if on:
                self._favorites.insert(0, track)
            self._save()

    def played(self, tracks: list[Track]) -> None:
        """Remember tracks as most recently played (newest first)."""
        if not tracks:
            return
        with self._lock:
            ids = {t.id for t in tracks}
            self._history = (tracks + [t for t in self._history if t.id not in ids])[
                :HISTORY_LIMIT
            ]
            self._save()

    def recent_ids(self) -> set[str]:
        with self._lock:
            return {t.id for t in self._history}

    # -- autoplay ------------------------------------------------------------

    def autoplay(self, speaker: str) -> bool:
        with self._lock:
            return speaker in self._autoplay

    def autoplay_speakers(self) -> list[str]:
        with self._lock:
            return sorted(self._autoplay)

    def set_autoplay(self, speaker: str, on: bool) -> None:
        with self._lock:
            (self._autoplay.add if on else self._autoplay.discard)(speaker)
            self._save()
