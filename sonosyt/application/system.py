"""Audio for the speakers, covers, and the facts and checks of the settings screen."""

from __future__ import annotations

import threading
import time

from ..domain.errors import MediaError
from ..domain.track import Track, watch_id
from .playback import PlaybackService
from .ports import AudioConverter, Network, SpeakerSystem, Thumbnails, Tools, VideoFeed


def version_key(version: str) -> tuple:
    """"0.2.25" -> (0, 2, 25); anything else (a local build) counts as oldest."""
    parts = str(version).split(".")
    return tuple(int(p) for p in parts) if all(p.isdigit() for p in parts) else ()


class StreamingService:
    """What the speakers fetch. Also notes who fetched, for the connection check."""

    def __init__(self, converter: AudioConverter, thumbnails: Thumbnails, video: VideoFeed | None = None):
        self.converter = converter
        self.thumbnails = thumbnails
        self.video_feed = video
        self._fetches: dict[str, float] = {}  # client address -> last audio request
        self._lock = threading.Lock()

    def note_fetch(self, client: str) -> None:
        with self._lock:
            self._fetches[client] = time.time()

    def last_fetch(self, client: str) -> float | None:
        """When ``client`` last fetched audio; else when anyone did (a stereo pair or
        group may fetch through another member)."""
        with self._lock:
            return self._fetches.get(client) or max(self._fetches.values(), default=None)

    def cached(self, video_id: str):
        return self.converter.cached(watch_id(video_id))

    def job(self, video_id: str):
        return self.converter.ensure(watch_id(video_id))

    def live(self, video_id: str):
        return self.converter.live(watch_id(video_id))

    def video(self, video_id: str, start: int = 0, end: int | None = None):
        """A piece of the silent video the phone shows while Sonos plays the sound."""
        if self.video_feed is None:
            raise MediaError("Kein Videostream verfügbar.")
        return self.video_feed.open(watch_id(video_id), max(0, start), end)

    def warm_video(self, video_id: str) -> None:
        if self.video_feed is not None:
            self.video_feed.warm(watch_id(video_id))

    def cover(self, video_id: str) -> bytes:
        return self.thumbnails.get(watch_id(video_id))


class SystemService:
    def __init__(self, version: str, speakers: SpeakerSystem, converter: AudioConverter, network: Network,
                 tools: Tools, playback: PlaybackService, streaming: StreamingService):
        self.version = version
        self.speakers = speakers
        self.converter = converter
        self.network = network
        self.tools = tools
        self.playback = playback
        self.streaming = streaming
        self._ytdlp: tuple[float, str] | None = None

    def info(self) -> dict:
        now = time.monotonic()
        if not self._ytdlp or now - self._ytdlp[0] > 600:  # asking yt-dlp takes a moment
            self._ytdlp = (now, self.tools.ytdlp_version())
        return {"version": self.version, "ytdlp": self._ytdlp[1], "cache": self.converter.cache_info()}

    def check_update(self) -> dict:
        """Is a newer server published? (Installing it is Unraid's or Docker's job.)"""
        latest = self.tools.latest_server_version()
        return {"current": self.version, "latest": latest,
                "newer": bool(latest) and version_key(latest) > version_key(self.version)}

    def clear_cache(self) -> dict:
        removed = self.converter.clear_cache()
        return {"removed": removed, **self.info()}

    def diagnose(self, speaker: str) -> dict:
        """Who can reach whom: server -> YouTube, server -> speaker, speaker -> server."""
        address = self.speakers.address(speaker)
        fetched = self.streaming.last_fetch(address)
        return {
            "youtube": self.network.youtube_reachable(),
            "speaker": self.speakers.reachable(speaker),
            "fetched_ago": None if fetched is None else int(time.time() - fetched),
            "stream_base": self.playback.base_url(address),
        }
