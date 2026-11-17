"""Playing videos on Sonos."""

from __future__ import annotations

import threading
import time

from ..domain.errors import MediaError
from ..domain.music import music_only
from ..domain.playback import PLAY_MODES, playback_error, position_to_remember, tracks_to_play
from ..domain.track import Track, watch_id
from .ports import AudioConverter, LibraryRepository, Network, SpeakerSystem
from .tracks import TrackCatalog


class PlaybackService:
    def __init__(
        self,
        speakers: SpeakerSystem,
        converter: AudioConverter,
        tracks: TrackCatalog,
        library: LibraryRepository,
        network: Network,
        *,
        port: int = 8327,
        public_url: str | None = None,
        prefetch_wait: float = 8.0,
        sleep=time.sleep,
    ):
        self.speakers = speakers
        self.converter = converter
        self.tracks = tracks
        self.library = library
        self.network = network
        self.port = port  # set to the real one once the HTTP server is bound
        self.public_url = public_url.rstrip("/") if public_url else None
        self.prefetch_wait = prefetch_wait
        self.sleep = sleep
        self._active: dict[str, float] = {}  # room -> when it was last played to
        self._lock = threading.Lock()

    # -- where the speakers fetch the audio --------------------------------------

    def base_url(self, speaker_address: str) -> str:
        return self.public_url or f"http://{self.network.local_address_for(speaker_address)}:{self.port}"

    def stream_url(self, track: Track, speaker_address: str) -> str:
        kind = "live" if track.live else "stream"
        return f"{self.base_url(speaker_address)}/{kind}/{track.id}.mp3"

    # -- playing -------------------------------------------------------------------

    def play(self, speaker: str, ids: list[str] | None, query: str | None, mode: str = "now") -> dict:
        if mode not in PLAY_MODES:
            raise ValueError("mode must be now, next or queue")
        if ids is not None:
            if not isinstance(ids, list) or not ids:
                raise ValueError("ids must be a list of YouTube video ids")
            tracks = [self.tracks.track(watch_id(str(i))) for i in ids]
        else:
            tracks = tracks_to_play(self.tracks.search(query or ""), query or "")
        if not tracks:
            raise MediaError("Nichts gefunden.")
        address = self.speakers.address(speaker)
        if mode == "now" and not tracks[0].live:
            # Short videos finish converting within seconds; then Sonos gets a
            # complete file (with duration and seeking) instead of a live stream.
            job = self.converter.ensure(tracks[0].id)
            job.done.wait(self.prefetch_wait)
            if job.done.is_set() and job.error:  # e.g. removed, private or age-restricted
                raise MediaError(playback_error(job.error))
        # The next few are converted ahead; later ones follow as playback gets there.
        self.converter.prefetch([t.id for t in tracks if not t.live][:4])
        position = self.speakers.enqueue(speaker, [(t, self.stream_url(t, address)) for t in tracks], mode)
        self.library.played(tracks)
        self.mark_active(speaker)
        resumed = self._resume(speaker, tracks[0]) if mode == "now" and len(tracks) == 1 else 0
        return {"queued": [t.to_dict() for t in tracks], "position": position, "resumed": resumed}

    def radio(self, speaker: str, video_id: str) -> dict:
        """This song now, similar songs after it, and autoplay on so it never ends."""
        seed = self.tracks.track(watch_id(str(video_id)))
        similar = [t for t in music_only(self.tracks.related(seed.id)) if t.id != seed.id and not t.live]
        result = self.play(speaker, [seed.id] + [t.id for t in similar[:24]], None, "now")
        self.library.set_autoplay(speaker, True)
        return {**result, "radio": seed.title}

    def enqueue(self, speaker: str, track: Track, mode: str = "queue") -> None:
        """Add one known track, without waiting for anything (used by autoplay)."""
        self.tracks.remember([track])
        self.converter.ensure(track.id)  # convert ahead, so it starts seamlessly
        self.speakers.enqueue(speaker, [(track, self.stream_url(track, self.speakers.address(speaker)))], mode)
        self.library.played([track])

    def _resume(self, speaker: str, track: Track) -> int:
        """Continue a long video where it was left. Returns the position, or 0."""
        seconds = self.library.position(track.id)
        if not seconds or track.live or not self.converter.cached(track.id):
            return 0  # seeking needs the complete file
        for _ in range(3):  # the speaker may still be switching tracks
            try:
                self.speakers.control(speaker, "seek", seconds)
                return seconds
            except Exception:  # noqa: BLE001
                self.sleep(1)
        return 0

    def remember_position(self, status: dict) -> None:
        """Note where a long video is, so it can be continued later."""
        found = position_to_remember(status)
        if found:
            self.library.set_position(*found)

    def status(self, speaker: str) -> dict:
        return {**self.speakers.status(speaker), "autoplay": self.library.autoplay(speaker)}

    # -- rooms in use ---------------------------------------------------------------

    def mark_active(self, speaker: str) -> None:
        with self._lock:
            self._active[speaker] = time.monotonic()

    def active_speakers(self, max_age: float = 12 * 3600) -> set[str]:
        now = time.monotonic()
        with self._lock:
            self._active = {s: t for s, t in self._active.items() if now - t < max_age}
            return set(self._active)
