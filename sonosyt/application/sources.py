"""Playing a music source (favourites or a playlist) in a room."""

from __future__ import annotations

import random

from ..domain.errors import MediaError
from ..domain.source import describe, normalize_source
from .playback import PlaybackService
from .ports import LibraryRepository, SpeakerSystem


class SourcePlayer:
    def __init__(self, library: LibraryRepository, speakers: SpeakerSystem, playback: PlaybackService):
        self.library = library
        self.speakers = speakers
        self.playback = playback

    def play(self, speaker: str, source: dict, shuffle: bool = False) -> str:
        """Start the source now. Returns what is playing, for messages."""
        source = normalize_source(source)   # also turns older Sonos favourites into favourites
        snapshot = self.library.snapshot()
        if source["type"] == "playlist":
            tracks = next((p["tracks"] for p in snapshot["playlists"] if p["name"] == source["name"]), None)
            if tracks is None:
                raise MediaError(f"Die Playlist „{source['name']}“ gibt es nicht mehr.")
        else:
            tracks = snapshot["favorites"]
        ids = [t["id"] for t in tracks if not t.get("live")]
        if not ids:
            raise MediaError(f"„{describe(source)}“ ist leer.")
        if shuffle:
            random.shuffle(ids)
        self.playback.play(speaker, ids[:100], None, "now")
        return describe(source)
