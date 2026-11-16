"""Presets: start saved music in saved rooms at a saved volume."""

from __future__ import annotations

import logging

from .ports import LibraryRepository, SpeakerSystem
from .rooms import RoomService
from .sources import SourcePlayer

log = logging.getLogger(__name__)


class PresetService:
    def __init__(self, library: LibraryRepository, speakers: SpeakerSystem, rooms: RoomService,
                 sources: SourcePlayer):
        self.library = library
        self.speakers = speakers
        self.rooms = rooms
        self.sources = sources

    def list(self) -> list[dict]:
        return self.library.presets()

    def save(self, data: dict) -> list[dict]:
        for room in (data or {}).get("rooms") or []:
            self.rooms.require(str(room))
        self.library.save_preset(dict(data))
        return self.list()

    def delete(self, preset_id: str) -> list[dict]:
        self.library.delete_preset(str(preset_id))
        return self.list()

    def run(self, preset_id: str) -> dict:
        preset = next((p for p in self.list() if p["id"] == preset_id), None)
        if preset is None:
            raise ValueError("Diese Voreinstellung gibt es nicht.")
        main, others = preset["rooms"][0], preset["rooms"][1:]
        current = {r["name"]: r for r in self.speakers.rooms(main)}
        for room in others:  # bring the other rooms into the main room's group
            if not current.get(room, {}).get("grouped"):
                try:
                    self.speakers.group(main, room, True)
                except Exception as exc:  # noqa: BLE001 - one room refusing must not stop the rest
                    log.warning("preset %s: %s did not join: %s", preset["name"], room, exc)
        if preset["volume"] is not None:
            self.speakers.set_volume(main, preset["volume"])
        playing = self.sources.play(main, preset["source"], preset["shuffle"])
        return {"playing": playing, "speaker": main}
