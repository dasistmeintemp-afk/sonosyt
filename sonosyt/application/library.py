"""Favourites, playlists, history and per-room autoplay."""

from __future__ import annotations

from ..domain.track import watch_id
from .ports import LibraryRepository
from .rooms import RoomService
from .tracks import TrackCatalog

PLAYLIST_OPS = ("add", "remove", "delete", "set")


class LibraryService:
    def __init__(self, repository: LibraryRepository, tracks: TrackCatalog, rooms: RoomService):
        self.repository = repository
        self.tracks = tracks
        self.rooms = rooms

    def snapshot(self) -> dict:
        return self.repository.snapshot()

    def set_favorite(self, video_id: str, on: bool = True) -> dict:
        self.repository.set_favorite(self.tracks.track(watch_id(str(video_id))), bool(on))
        return self.snapshot()

    def clear_history(self) -> dict:
        self.repository.clear_history()
        return self.snapshot()

    def edit_playlist(self, name: str, op: str = "add", video_id=None, ids=None) -> dict:
        if op == "add":
            self.repository.playlist_add(name, self.tracks.track(watch_id(str(video_id))))
        elif op == "remove":
            self.repository.playlist_remove(name, str(video_id))
        elif op == "delete":
            self.repository.playlist_delete(name)
        elif op == "set":
            if not isinstance(ids, list) or not ids:
                raise ValueError("ids must be a non-empty list")
            self.repository.playlist_set(name, [self.tracks.track(watch_id(str(i))) for i in ids[:500]])
        else:
            raise ValueError("op must be add, remove, delete or set")
        return self.snapshot()

    def set_autoplay(self, speaker: str, on: bool) -> dict:
        self.rooms.require(speaker)
        self.repository.set_autoplay(speaker, bool(on))
        return {"autoplay": self.repository.autoplay(speaker)}
