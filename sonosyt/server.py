"""HTTP server: audio streams for the speakers, JSON API and web UI for people."""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__, youtube
from .sonos import SonosController, SonosError, local_ip_for
from .transcode import Transcoder
from .youtube import Track, YouTubeError

log = logging.getLogger(__name__)

STREAM_RE = re.compile(r"^/stream/([A-Za-z0-9_-]{11})\.mp3$")
MAX_BODY = 64 * 1024


class App:
    """Everything the request handlers need."""

    def __init__(
        self,
        transcoder: Transcoder,
        sonos: SonosController,
        port: int,
        public_url: str | None = None,
        lookup=youtube.lookup,
        prefetch_wait: float = 8.0,
    ):
        self.transcoder = transcoder
        self.sonos = sonos
        self.port = port
        self.public_url = public_url.rstrip("/") if public_url else None
        self.lookup = lookup
        self.prefetch_wait = prefetch_wait
        self._tracks: dict[str, Track] = {}
        self._lock = threading.Lock()

    def remember(self, tracks: list[Track]) -> None:
        with self._lock:
            for t in tracks:
                self._tracks[t.id] = t
            while len(self._tracks) > 2000:
                self._tracks.pop(next(iter(self._tracks)))

    def search(self, query: str) -> list[Track]:
        tracks = self.lookup(query)
        self.remember(tracks)
        return tracks

    def track(self, video_id: str) -> Track:
        with self._lock:
            known = self._tracks.get(video_id)
        if known:
            return known
        tracks = self.search(video_id)
        if not tracks:
            raise YouTubeError(f"Video nicht gefunden: {video_id}")
        return tracks[0]

    def stream_url(self, video_id: str, speaker_ip: str) -> str:
        base = self.public_url or f"http://{local_ip_for(speaker_ip)}:{self.port}"
        return f"{base}/stream/{video_id}.mp3"

    def play(self, speaker: str, ids: list[str] | None, query: str | None, play_now: bool) -> dict:
        if ids:
            tracks = [self.track(i) for i in ids]
        else:
            tracks = self.search(query or "")
            # A search term means "the best hit"; a playlist URL means all of it.
            if tracks and not re.match(r"^https?://", (query or "").strip(), re.I):
                tracks = tracks[:1]
        if not tracks:
            raise YouTubeError("Nichts gefunden.")
        device = self.sonos.get(speaker)
        if play_now:
            # Short videos finish converting within seconds; then Sonos gets a
            # complete file (with duration and seeking) instead of a live stream.
            self.transcoder.ensure(tracks[0].id).done.wait(self.prefetch_wait)
        items = [(t, self.stream_url(t.id, device.ip_address)) for t in tracks]
        position = self.sonos.enqueue(speaker, items, play_now)
        return {"queued": [t.to_dict() for t in tracks], "position": position}


class Handler(BaseHTTPRequestHandler):
    server_version = "sonosyt"
    app: App  # set by make_server

    def log_message(self, fmt, *args):  # route through logging
        log.info("%s %s", self.address_string(), fmt % args)

    # -- helpers -----------------------------------------------------------

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request body too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    def _guard(self, fn) -> None:
        """Run an API handler, mapping failures to JSON errors."""
        try:
            self._json(fn())
        except (ValueError, KeyError, TypeError) as exc:
            self._error(400, f"Ungültige Anfrage: {exc}")
        except SonosError as exc:
            self._error(404, str(exc))
        except YouTubeError as exc:
            self._error(502, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - e.g. SoCo/UPnP errors
            log.exception("request failed")
            self._error(500, f"{exc.__class__.__name__}: {exc}")

    # -- routing -----------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        m = STREAM_RE.match(url.path)
        if m:
            return self._stream(m.group(1))
        if url.path in ("/", "/index.html"):
            return self._index()
        if url.path == "/api/ping":  # lets the Android app recognise the server
            return self._json({"app": "sonosyt", "version": __version__})
        if url.path == "/api/speakers":
            refresh = query.get("refresh", ["0"])[0] == "1"
            return self._guard(lambda: self.app.sonos.speakers(refresh))
        if url.path == "/api/search":
            q = query.get("q", [""])[0]
            return self._guard(lambda: [t.to_dict() for t in self.app.search(q)])
        if url.path == "/api/status":
            speaker = query.get("speaker", [""])[0]
            return self._guard(lambda: self.app.sonos.status(speaker))
        self._error(404, "not found")

    def do_POST(self):
        path = urlsplit(self.path).path
        # Browsers cannot send JSON cross-site without a CORS preflight, which
        # this server never answers - so other websites can't drive the speakers.
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error(415, "Content-Type must be application/json")
        if path == "/api/play":
            def play():
                body = self._body()
                ids = body.get("ids")
                if ids is not None and not (
                    isinstance(ids, list)
                    and ids
                    and all(isinstance(i, str) and youtube.VIDEO_ID_RE.match(i) for i in ids)
                ):
                    raise ValueError("ids must be a list of YouTube video ids")
                return self.app.play(
                    str(body["speaker"]), ids, body.get("query"), not body.get("queue", False)
                )
            return self._guard(play)
        if path == "/api/control":
            def control():
                body = self._body()
                speaker = str(body["speaker"])
                if "volume" in body:
                    self.app.sonos.set_volume(speaker, int(body["volume"]))
                if body.get("action"):
                    self.app.sonos.control(speaker, str(body["action"]))
                return {"ok": True}
            return self._guard(control)
        self._error(404, "not found")

    # -- web UI ------------------------------------------------------------

    def _index(self) -> None:
        body = resources.files("sonosyt").joinpath("web/index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- audio -------------------------------------------------------------

    def _stream(self, video_id: str) -> None:
        tc = self.app.transcoder
        try:
            path = tc.cached(video_id)
            if path:
                return self._send_file(path)
            job = tc.ensure(video_id)
            # A resume/seek needs the complete file; give the conversion a moment.
            wants_offset = bool(re.match(r"bytes=[1-9]", self.headers.get("Range") or ""))
            if wants_offset:
                job.done.wait(20)
            if not job.wait_for_data(60):
                return self._error(502, job.error or "Timeout bei der Umwandlung")
            if job.ok:
                return self._send_file(job.final)
            # Still converting: stream what exists and follow the growing file.
            # No Content-Length - the body ends when the connection closes.
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Accept-Ranges", "none")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if self.command == "HEAD":
                return
            for chunk in job.follow():
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # speaker skipped the track or re-requested it

    def _send_file(self, path: Path) -> None:
        size = path.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        m = re.match(r"^bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:  # suffix range: the last N bytes
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def make_server(app: App, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, app.port), handler)
    server.daemon_threads = True
    app.port = server.server_address[1]  # resolves port 0
    return server


def check_ffmpeg(ffmpeg: str = "ffmpeg") -> None:
    if not shutil.which(ffmpeg):
        raise SystemExit(
            "ffmpeg wurde nicht gefunden. Bitte installieren "
            "(z. B. `sudo apt install ffmpeg` oder `brew install ffmpeg`)."
        )
