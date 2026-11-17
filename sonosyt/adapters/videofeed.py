"""The picture of a YouTube video for the phone, relayed by the server.

Implements the VideoFeed port. yt-dlp finds the address of a video-only stream
(no sound - that comes from Sonos), and the server relays it piece by piece.
This works for every video, including the many music videos whose owners do
not allow YouTube's embedded player.

The server relays rather than handing the phone YouTube's address because
that address is tied to the machine that asked for it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

from ..application.ports import VideoPiece
from ..domain.errors import MediaError
from ..domain.track import watch_url

# Widely playable H.264 up to 720p first; anything plain-HTTP after that.
FORMAT = ("bv*[height<=720][vcodec^=avc1][protocol=https]/bv*[height<=720][protocol=https]"
          "/b[height<=720][protocol=https]/b[protocol=https]")
PIECE = 4 * 1024 * 1024   # YouTube slows down or refuses much larger single requests
LIFETIME = 3 * 3600.0     # its addresses expire after about six hours
KEEP = 32                 # resolved videos remembered


@dataclass(frozen=True)
class Source:
    url: str
    headers: dict
    content_type: str


def resolve(video_id: str) -> Source:
    """Ask yt-dlp for the address of a video-only stream of ``video_id``."""
    cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", "--no-warnings",
           "-f", FORMAT, "--", watch_url(video_id)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("YouTube antwortet nicht (Zeitüberschreitung).") from exc
    if proc.returncode != 0:
        lines = (proc.stderr or "").strip().splitlines()
        raise MediaError(lines[-1] if lines else "yt-dlp ist fehlgeschlagen.")
    try:
        info = json.loads(proc.stdout)
    except ValueError as exc:
        raise MediaError("Unerwartete Antwort von yt-dlp.") from exc
    url = info.get("url") or ""
    if not url.startswith("https://"):
        raise MediaError("Für dieses Video gibt es keinen passenden Videostream.")
    ext = info.get("ext") or "mp4"
    return Source(url, dict(info.get("http_headers") or {}),
                  "video/webm" if ext == "webm" else "video/mp4")


def _chunks(resp):
    try:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                return
            yield chunk
    finally:
        resp.close()


class YouTubeVideoFeed:
    def __init__(self, resolve=resolve, opener=None, clock=time.monotonic):
        self.resolve = resolve
        self.opener = opener or (lambda req: urllib.request.urlopen(req, timeout=20))
        self.clock = clock
        self._sources: OrderedDict[str, tuple[Source, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._resolving: dict[str, threading.Lock] = {}

    def _source(self, video_id: str, fresh: bool = False) -> Source:
        with self._lock:
            gate = self._resolving.setdefault(video_id, threading.Lock())
        with gate:  # one yt-dlp per video, however many requests arrive at once
            with self._lock:
                known = self._sources.get(video_id)
                if known and not fresh and known[1] > self.clock():
                    self._sources.move_to_end(video_id)
                    return known[0]
            source = self.resolve(video_id)
            with self._lock:
                self._sources[video_id] = (source, self.clock() + LIFETIME)
                self._sources.move_to_end(video_id)
                while len(self._sources) > KEEP:
                    old, _ = self._sources.popitem(last=False)
                    self._resolving.pop(old, None)
            return source

    def warm(self, video_id: str) -> None:
        self._source(video_id)

    def open(self, video_id: str, start: int, end: int | None) -> VideoPiece:
        stop = start + PIECE - 1 if end is None else min(end, start + PIECE - 1)
        for attempt in (0, 1):
            source = self._source(video_id, fresh=attempt == 1)
            req = urllib.request.Request(source.url, headers={**source.headers, "Range": f"bytes={start}-{stop}"})
            try:
                resp = self.opener(req)
            except urllib.error.HTTPError as exc:
                if exc.code == 416:
                    raise ValueError("Bereich außerhalb des Videos") from exc
                if exc.code in (403, 404, 410) and attempt == 0:
                    continue  # the address expired: look it up again
                raise MediaError(f"YouTube liefert das Video nicht (HTTP {exc.code}).") from exc
            except OSError as exc:
                raise MediaError(f"YouTube nicht erreichbar: {exc}") from exc
            m = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)", resp.headers.get("Content-Range") or "")
            if m:
                first, last = int(m.group(1)), int(m.group(2))
                size = int(m.group(3)) if m.group(3) != "*" else None
            else:  # whole file in one answer
                length = int(resp.headers.get("Content-Length") or 0)
                first, last, size = 0, max(0, length - 1), length or None
            return VideoPiece(first, last, size, source.content_type, _chunks(resp))
        raise MediaError("YouTube liefert das Video nicht.")
