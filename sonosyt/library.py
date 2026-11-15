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
        self._playlists: dict[str, list[Track]] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return

        def tracks(key, source=data) -> list[Track]:
            out = []
            for item in source.get(key) or []:
                try:
                    out.append(Track.from_dict(item))
                except (KeyError, TypeError, ValueError):
                    continue
            return out

        self._favorites = tracks("favorites")
        self._history = tracks("history")[:HISTORY_LIMIT]
        self._autoplay = {str(name) for name in data.get("autoplay") or []}
        for entry in data.get("playlists") or []:
            if isinstance(entry, dict) and entry.get("name"):
                self._playlists[str(entry["name"])] = tracks("tracks", entry)

    def _save(self) -> None:
        data = {
            "favorites": [t.to_dict() for t in self._favorites],
            "history": [t.to_dict() for t in self._history],
            "autoplay": sorted(self._autoplay),
            "playlists": self._playlist_dicts(),
        }
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # a read-only data directory must not break playback

    # -- favourites / history ----------------------------------------------

    def _playlist_dicts(self) -> list[dict]:
        return [
            {"name": name, "tracks": [t.to_dict() for t in tracks]}
            for name, tracks in self._playlists.items()
        ]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "favorites": [t.to_dict() for t in self._favorites],
                "history": [t.to_dict() for t in self._history],
                "playlists": self._playlist_dicts(),
            }

    def tracks(self) -> list[Track]:
        with self._lock:
            out = self._favorites + self._history
            for tracks in self._playlists.values():
                out = out + tracks
            return out

    # -- playlists -------------------------------------------------------------

    @staticmethod
    def _playlist_name(name: str) -> str:
        name = " ".join(str(name).split())
        if not 1 <= len(name) <= 60:
            raise ValueError("Der Playlist-Name muss 1 bis 60 Zeichen lang sein.")
        return name

    def playlist_add(self, name: str, track: Track) -> None:
        """Append a track to a playlist, creating the playlist if needed."""
        name = self._playlist_name(name)
        with self._lock:
            tracks = self._playlists.setdefault(name, [])
            if all(t.id != track.id for t in tracks):
                tracks.append(track)
            self._save()

    def playlist_remove(self, name: str, video_id: str) -> None:
        name = self._playlist_name(name)
        with self._lock:
            if name in self._playlists:
                self._playlists[name] = [t for t in self._playlists[name] if t.id != video_id]
                self._save()

    def playlist_delete(self, name: str) -> None:
        name = self._playlist_name(name)
        with self._lock:
            self._playlists.pop(name, None)
            self._save()

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
