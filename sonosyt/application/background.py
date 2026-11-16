"""What runs by itself: autoplay, converting ahead, resume positions, alarms."""

from __future__ import annotations

import logging
import threading
import time

from ..domain.playback import pick_autoplay, upcoming_video, wants_autoplay
from ..domain.track import Track
from .alarms import AlarmService
from .playback import PlaybackService
from .ports import AudioConverter, LibraryRepository, SpeakerSystem
from .tracks import TrackCatalog

log = logging.getLogger(__name__)
INTERVAL = 8.0


class AutoplayService:
    """When the last queued track plays, add what YouTube would play next."""

    def __init__(self, speakers: SpeakerSystem, tracks: TrackCatalog, library: LibraryRepository,
                 playback: PlaybackService):
        self.speakers = speakers
        self.tracks = tracks
        self.library = library
        self.playback = playback
        self._handled: dict[str, str] = {}  # room -> video autoplay last looked at

    def once(self, speaker: str, status: dict | None = None) -> Track | None:
        status = status or self.speakers.status(speaker)
        if not wants_autoplay(status, self._handled.get(speaker)):
            return None
        video_id = status["video_id"]
        self._handled[speaker] = video_id  # one attempt per track
        choice = pick_autoplay(self.tracks.related(video_id), self.library.recent_ids() | {video_id})
        if choice is None:
            return None
        self.playback.enqueue(speaker, choice, "queue")
        log.info("autoplay in %s: %s", speaker, choice.title)
        return choice


class Monitor:
    """Looks after the rooms in use every few seconds."""

    def __init__(self, speakers: SpeakerSystem, converter: AudioConverter, library: LibraryRepository,
                 playback: PlaybackService, autoplay: AutoplayService, alarms: AlarmService,
                 interval: float = INTERVAL):
        self.speakers = speakers
        self.converter = converter
        self.library = library
        self.playback = playback
        self.autoplay = autoplay
        self.alarms = alarms
        self.interval = interval
        self._next_checked: dict[str, tuple] = {}

    def prefetch_next(self, speaker: str, status: dict) -> str | None:
        """Convert the track after the playing one, so it starts without a gap."""
        key = (status.get("video_id"), status.get("queue_position"), status.get("queue_length"))
        if status.get("state") != "PLAYING" or self._next_checked.get(speaker) == key:
            return None
        position, length = status.get("queue_position") or 0, status.get("queue_length") or 0
        if not 0 < position < length:
            return None
        self._next_checked[speaker] = key
        video_id = upcoming_video(status, self.speakers.queue(speaker))
        if video_id:
            self.converter.prefetch([video_id])
        return video_id

    def once(self) -> None:
        try:
            self.alarms.check()
        except Exception as exc:  # noqa: BLE001
            log.warning("alarm check failed: %s", exc)
        autoplay = set(self.library.autoplay_speakers())
        for speaker in sorted(autoplay | self.playback.active_speakers()):
            try:
                status = self.speakers.status(speaker)
                self.playback.remember_position(status)
                self.prefetch_next(speaker, status)
                if speaker in autoplay:
                    self.autoplay.once(speaker, status)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("monitoring %s failed: %s", speaker, exc)

    def start(self) -> threading.Thread:
        def loop() -> None:
            while True:
                self.once()
                time.sleep(self.interval)

        thread = threading.Thread(target=loop, name="monitor", daemon=True)
        thread.start()
        return thread
