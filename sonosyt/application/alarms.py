"""Alarms: start music in a room at a set time, softly getting louder."""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timezone

from ..domain.alarm import occurrence, ramp, start_volume
from ..domain.errors import MediaError
from .playback import PlaybackService
from .ports import LibraryRepository, SpeakerSystem
from .rooms import RoomService

log = logging.getLogger(__name__)


def _in_background(fn, name: str) -> None:
    threading.Thread(target=fn, name=name, daemon=True).start()


class AlarmService:
    def __init__(self, library: LibraryRepository, speakers: SpeakerSystem, playback: PlaybackService,
                 rooms: RoomService, *, sleep=time.sleep, spawn=_in_background):
        self.library = library
        self.speakers = speakers
        self.playback = playback
        self.rooms = rooms
        self.sleep = sleep
        self.spawn = spawn
        self._rung: dict[str, str] = {}  # alarm id -> occurrence it last rang for

    # -- managing --------------------------------------------------------------------

    def list(self) -> list[dict]:
        return self.library.alarms()

    def save(self, data: dict) -> list[dict]:
        self.rooms.require(str((data or {}).get("speaker") or ""))
        self.library.save_alarm(dict(data))
        return self.list()

    def delete(self, alarm_id: str) -> list[dict]:
        self.library.delete_alarm(str(alarm_id))
        return self.list()

    def test(self, alarm_id: str) -> dict:
        alarm = next((a for a in self.list() if a["id"] == alarm_id), None)
        if alarm is None:
            raise ValueError("Diesen Wecker gibt es nicht.")
        # Rung as a repeating alarm, so testing never switches a one-off alarm off.
        return {"playing": self.fire({**alarm, "days": [0]}, ramp_seconds=20)}

    # -- ringing ---------------------------------------------------------------------

    def due(self, now: datetime | None = None) -> list[dict]:
        """Alarms whose minute has come and that have not rung for it yet."""
        now = now or datetime.now(timezone.utc)
        found = []
        for alarm in self.list():
            key = occurrence(alarm, now)
            if key and self._rung.get(alarm["id"]) != key:
                self._rung[alarm["id"]] = key
                found.append(alarm)
        return found

    def fire(self, alarm: dict, ramp_seconds: float = 90.0) -> str:
        """Start what an alarm plays. Returns a short description of it."""
        speaker, source = alarm["speaker"], alarm["source"]
        first = start_volume(alarm)
        self.speakers.set_volume(speaker, first)
        if source["type"] == "sonosfav":
            what = self.speakers.play_favorite(speaker, source["index"])
        else:
            snapshot = self.library.snapshot()
            if source["type"] == "playlist":
                tracks = next((p["tracks"] for p in snapshot["playlists"] if p["name"] == source["name"]), None)
                if tracks is None:
                    raise MediaError(f"Die Playlist „{source['name']}“ gibt es nicht mehr.")
                what = source["name"]
            else:
                tracks, what = snapshot["favorites"], "Favoriten"
            ids = [t["id"] for t in tracks if not t.get("live")]
            if not ids:
                raise MediaError(f"„{what}“ ist leer.")
            if alarm["shuffle"]:
                random.shuffle(ids)
            self.playback.play(speaker, ids[:100], None, "now")
        levels = ramp(first, alarm["volume"]) if alarm["fade"] else []
        if levels:
            def louder():
                for level in levels:
                    self.sleep(ramp_seconds / len(levels))
                    try:
                        self.speakers.set_volume(speaker, level)
                    except Exception:  # noqa: BLE001 - speaker busy: try the next step
                        continue
            self.spawn(louder, "alarm-fade")
        if not alarm["days"]:
            self.library.disable_alarm(alarm["id"])  # a one-off alarm is done
        log.info("alarm %s in %s: %s", alarm["time"], speaker, what)
        return what

    def check(self, now: datetime | None = None) -> None:
        for alarm in self.due(now):
            def ring(alarm=alarm):
                try:
                    self.fire(alarm)
                except Exception as exc:  # noqa: BLE001
                    log.warning("alarm %s failed: %s", alarm["time"], exc)
            self.spawn(ring, "alarm")
