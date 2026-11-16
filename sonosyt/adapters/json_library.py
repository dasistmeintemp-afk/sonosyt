"""Favourites, history, playlists, alarms and per-room settings in one JSON file.

Implements the LibraryRepository port.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..domain.alarm import normalize_alarm
from ..domain.track import Track

HISTORY_LIMIT = 50


class Library:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._favorites: list[Track] = []
        self._history: list[Track] = []
        self._autoplay: set[str] = set()
        self._playlists: dict[str, list[Track]] = {}
        self._positions: dict[str, int] = {}  # video id -> seconds, to resume long videos
        self._alarms: list[dict] = []
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
        positions = data.get("positions")
        if isinstance(positions, dict):
            for video_id, seconds in positions.items():
                try:
                    self._positions[str(video_id)] = int(seconds)
                except (TypeError, ValueError):
                    continue
        for alarm in data.get("alarms") or []:
            try:
                self._alarms.append(normalize_alarm(alarm))
            except (ValueError, TypeError, KeyError):
                continue
        for entry in data.get("playlists") or []:
            if isinstance(entry, dict) and entry.get("name"):
                self._playlists[str(entry["name"])] = tracks("tracks", entry)

    def _save(self) -> None:
        data = {
            "favorites": [t.to_dict() for t in self._favorites],
            "history": [t.to_dict() for t in self._history],
            "autoplay": sorted(self._autoplay),
            "playlists": self._playlist_dicts(),
            "positions": self._positions,
            "alarms": self._alarms,
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

    def playlist_set(self, name: str, tracks: list[Track]) -> None:
        """Create or replace a playlist with these tracks (duplicates dropped)."""
        name = self._playlist_name(name)
        seen: set[str] = set()
        unique = [t for t in tracks if not (t.id in seen or seen.add(t.id))]
        with self._lock:
            self._playlists[name] = unique[:500]
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

    def clear_history(self) -> None:
        with self._lock:
            self._history = []
            self._save()

    def recent_ids(self) -> set[str]:
        with self._lock:
            return {t.id for t in self._history}

    # -- alarms ------------------------------------------------------------------

    def alarms(self) -> list[dict]:
        with self._lock:
            return [dict(a) for a in self._alarms]

    def save_alarm(self, data: dict) -> dict:
        alarm = normalize_alarm(data)
        with self._lock:
            for i, old in enumerate(self._alarms):
                if old["id"] == alarm["id"]:
                    self._alarms[i] = alarm
                    break
            else:
                if len(self._alarms) >= 20:
                    raise ValueError("Höchstens 20 Wecker.")
                self._alarms.append(alarm)
            self._alarms.sort(key=lambda a: a["time"])
            self._save()
        return alarm

    def delete_alarm(self, alarm_id: str) -> None:
        with self._lock:
            self._alarms = [a for a in self._alarms if a["id"] != alarm_id]
            self._save()

    def disable_alarm(self, alarm_id: str) -> None:
        with self._lock:
            for alarm in self._alarms:
                if alarm["id"] == alarm_id:
                    alarm["enabled"] = False
            self._save()

    # -- resume positions ------------------------------------------------------

    def position(self, video_id: str) -> int:
        with self._lock:
            return self._positions.get(video_id, 0)

    def set_position(self, video_id: str, seconds: int) -> None:
        """Remember where a long video was left; 0 forgets it."""
        with self._lock:
            old = self._positions.get(video_id, 0)
            if seconds <= 0:
                if not old:
                    return
                del self._positions[video_id]
            else:
                if abs(seconds - old) < 20:
                    return  # not worth a write
                self._positions.pop(video_id, None)
                self._positions[video_id] = int(seconds)
                while len(self._positions) > 200:
                    self._positions.pop(next(iter(self._positions)))
            self._save()

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

