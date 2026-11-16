"""Rooms: discovery, transport controls, volume, grouping, tone and Sonos favourites."""

from __future__ import annotations

from .playback import PlaybackService
from .ports import SpeakerSystem


class RoomService:
    def __init__(self, speakers: SpeakerSystem, playback: PlaybackService):
        self.speakers = speakers
        self.playback = playback

    def list(self, refresh: bool = False) -> list[dict]:
        return self.speakers.speakers(refresh)

    def overview(self) -> list[dict]:
        """Every playing group with what it plays and its volume, for the rooms tab."""
        return self.speakers.overview()

    def pause_all(self) -> dict:
        paused = []
        for group in self.speakers.overview():
            if group.get("state") in ("PLAYING", "TRANSITIONING"):
                try:
                    self.speakers.control(group["name"], "pause")
                    paused.append(group["name"])
                except Exception:  # noqa: BLE001 - a room that is busy: the others still pause
                    continue
        return {"paused": paused}

    def control(self, speaker: str, action: str | None = None, value=None, to=None,
                volume=None, single: bool = False) -> None:
        if volume is not None:
            self.speakers.set_volume(speaker, int(volume), bool(single))
        if action:
            self.speakers.control(speaker, str(action), None if value is None else int(value),
                                  None if to is None else int(to))

    def queue(self, speaker: str) -> list[dict]:
        return self.speakers.queue(speaker)

    def rooms(self, speaker: str) -> list[dict]:
        return self.speakers.rooms(speaker)

    def group(self, speaker: str, room: str, join: bool) -> list[dict]:
        self.speakers.group(speaker, room, join)
        return self.speakers.rooms(speaker)

    def set_eq(self, room: str, bass=None, treble=None, loudness=None) -> dict:
        return self.speakers.set_eq(room, bass, treble, loudness)

    def favorites(self, speaker: str) -> list[dict]:
        return self.speakers.favorites(speaker)

    def play_favorite(self, speaker: str, index: int) -> dict:
        title = self.speakers.play_favorite(speaker, int(index))
        self.playback.mark_active(speaker)
        return {"title": title}

    def require(self, speaker: str) -> None:
        """Raises SpeakerError unless the room exists."""
        self.speakers.address(speaker)
