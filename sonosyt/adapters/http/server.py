"""HTTP server: the JSON API and web UI for people, and the audio for the speakers.

Inbound adapter. Every route only translates between HTTP and a use case of the
Application; no decisions about playing, alarms or the library are made here.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ... import __version__
from ...application.app import Application
from ...domain.errors import MediaError, SpeakerError

log = logging.getLogger(__name__)

THUMB_RE = re.compile(r"^/thumb/([A-Za-z0-9_-]{11})\.jpg$")
STREAM_RE = re.compile(r"^/(stream|live)/([A-Za-z0-9_-]{11})\.mp3$")
VIDEO_RE = re.compile(r"^/video/([A-Za-z0-9_-]{11})\.mp4$")
MAX_BODY = 64 * 1024

# Host names a browser may use for this server. Anything else is refused, so a
# website that points its own domain at the server's address ("DNS rebinding")
# cannot control the speakers from a visitor's browser.
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".home.arpa", ".localdomain",
                  ".fritz.box", ".box")


def host_allowed(header: str | None, extra: frozenset[str] = frozenset()) -> bool:
    if not header:
        return True  # HTTP/1.0 clients such as some speakers send none
    host = header.strip().lower()
    if host.startswith("["):  # IPv6 literal
        return True
    host = host.rsplit(":", 1)[0] if ":" in host else host
    if not host or "." not in host or host in extra or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _q(query: dict, name: str, default: str = "") -> str:
    return query.get(name, [default])[0]


def get_routes(app: Application) -> dict:
    """GET /api/... -> function(query) returning JSON-able data."""
    return {
        "/api/ping": lambda q: {"app": "sonosyt", "version": __version__},
        "/api/speakers": lambda q: app.rooms.list(_q(q, "refresh", "0") == "1"),
        "/api/search": lambda q: [t.to_dict() for t in app.tracks.search(
            _q(q, "q"), prefetch=True, limit=max(1, min(30, int(_q(q, "limit", "10") or 10))))],
        "/api/related": lambda q: [t.to_dict() for t in app.tracks.related(_q(q, "id"))],
        "/api/suggest": lambda q: app.tracks.suggest(_q(q, "q")),
        "/api/status": lambda q: app.playback.status(_q(q, "speaker")),
        "/api/queue": lambda q: app.rooms.queue(_q(q, "speaker")),
        "/api/rooms": lambda q: app.rooms.rooms(_q(q, "speaker")),
        "/api/sonosfav": lambda q: app.rooms.favorites(_q(q, "speaker")),
        "/api/library": lambda q: app.library.snapshot(),
        "/api/alarms": lambda q: app.alarms.list(),
        "/api/presets": lambda q: app.presets.list(),
        "/api/system": lambda q: app.rooms.overview(),
        "/api/charts": lambda q: app.charts.get(),
        "/api/lyrics": lambda q: app.lyrics.get(app.tracks.track(_q(q, "id"))),
        "/api/info": lambda q: app.system.info(),
        "/api/update": lambda q: app.system.check_update(),
        "/api/diagnose": lambda q: app.system.diagnose(_q(q, "speaker")),
    }


def post_routes(app: Application) -> dict:
    """POST /api/... -> function(body) returning JSON-able data."""

    def play(b):
        mode = b.get("mode") or ("queue" if b.get("queue") else "now")
        return app.playback.play(str(b["speaker"]), b.get("ids"), b.get("query"), mode)

    def control(b):
        app.rooms.control(str(b["speaker"]), b.get("action"), b.get("value"), b.get("to"),
                          b.get("volume"), bool(b.get("single")))
        return {"ok": True}

    def alarms(b):
        op = b.get("op") or "save"
        if op == "save":
            return app.alarms.save(b["alarm"])
        if op == "delete":
            return app.alarms.delete(b["id"])
        if op == "test":
            return app.alarms.test(b["id"])
        raise ValueError("op must be save, delete or test")

    def presets(b):
        op = b.get("op") or "save"
        if op == "save":
            return app.presets.save(b["preset"])
        if op == "delete":
            return app.presets.delete(b["id"])
        if op == "run":
            return app.presets.run(b["id"])
        raise ValueError("op must be save, delete or run")

    return {
        "/api/play": play,
        "/api/presets": presets,
        "/api/pauseall": lambda b: app.rooms.pause_all(),
        "/api/control": control,
        "/api/group": lambda b: app.rooms.group(str(b["speaker"]), str(b["room"]), bool(b.get("join"))),
        "/api/eq": lambda b: app.rooms.set_eq(str(b["room"]), b.get("bass"), b.get("treble"), b.get("loudness")),
        "/api/sonosfav/play": lambda b: app.rooms.play_favorite(str(b["speaker"]), b["index"]),
        "/api/favorite": lambda b: app.library.set_favorite(b["id"], b.get("favorite", True)),
        "/api/playlist": lambda b: app.library.edit_playlist(
            str(b["name"]), str(b.get("op") or "add"), b.get("id"), b.get("ids")),
        "/api/history/clear": lambda b: app.library.clear_history(),
        "/api/autoplay": lambda b: app.library.set_autoplay(str(b["speaker"]), bool(b.get("on"))),
        "/api/alarms": alarms,
        "/api/cache/clear": lambda b: app.system.clear_cache(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "sonosyt"
    # set by make_server:
    app: Application
    gets: dict
    posts: dict
    allowed_hosts: frozenset = frozenset()

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
        """Run a use case, mapping its failures to JSON errors."""
        try:
            self._json(fn())
        except (ValueError, KeyError, TypeError) as exc:
            self._error(400, f"Ungültige Anfrage: {exc}")
        except SpeakerError as exc:
            self._error(404, str(exc))
        except MediaError as exc:
            self._error(502, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - e.g. SoCo/UPnP errors
            if isinstance(exc, OSError) or exc.__class__.__module__.startswith(("soco", "requests")):
                # A speaker that is off or busy is everyday business, not a crash.
                log.warning("%s failed: %s: %s", self.path, exc.__class__.__name__, exc)
            else:
                log.exception("request failed")
            self._error(500, f"{exc.__class__.__name__}: {exc}")

    def _refuse_foreign_host(self) -> bool:
        if host_allowed(self.headers.get("Host"), self.allowed_hosts):
            return False
        self._error(403, "Dieser Hostname ist nicht freigegeben. Den Server über seine IP-Adresse "
                         "aufrufen oder mit --allow-host <name> starten.")
        return True

    # -- routing -----------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self._refuse_foreign_host():
            return
        url = urlsplit(self.path)
        m = THUMB_RE.match(url.path)
        if m:
            return self._thumb(m.group(1))
        m = VIDEO_RE.match(url.path)
        if m:
            return self._video(m.group(1))
        m = STREAM_RE.match(url.path)
        if m:
            self.app.streaming.note_fetch(self.client_address[0])
            return self._live(m.group(2)) if m.group(1) == "live" else self._stream(m.group(2))
        if url.path in ("/", "/index.html"):
            return self._index()
        route = self.gets.get(url.path)
        if route is None:
            return self._error(404, "not found")
        query = parse_qs(url.query)
        self._guard(lambda: route(query))

    def do_POST(self):
        if self._refuse_foreign_host():
            return
        # Browsers cannot send JSON cross-site without a CORS preflight, which
        # this server never answers - so other websites can't drive the speakers.
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error(415, "Content-Type must be application/json")
        route = self.posts.get(urlsplit(self.path).path)
        if route is None:
            return self._error(404, "not found")
        self._guard(lambda: route(self._body()))

    # -- web UI ------------------------------------------------------------

    def _index(self) -> None:
        body = resources.files(__package__).joinpath("web/index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _thumb(self, video_id: str) -> None:
        """Cover images from our own origin, so the page may read their colours."""
        try:
            data = self.app.streaming.cover(video_id)
        except Exception:  # noqa: BLE001 - no cover: the page shows a placeholder
            return self._error(404, "kein Cover")
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # -- video for the phone -----------------------------------------------

    def _video(self, video_id: str) -> None:
        """The silent picture, relayed from YouTube in pieces (always 206 Partial Content).

        HEAD only looks the video up, so a later GET starts at once.
        """
        streaming = self.app.streaming
        try:
            if self.command == "HEAD":
                streaming.warm_video(video_id)
                self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = 0, None
            m = re.match(r"^bytes=(\d+)-(\d*)$", (self.headers.get("Range") or "").strip())
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else None
            piece = streaming.video(video_id, start, end)
        except ValueError:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        except MediaError as exc:
            return self._error(502, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", piece.content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(piece.end - piece.start + 1))
            self.send_header("Content-Range", f"bytes {piece.start}-{piece.end}/{piece.size or '*'}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for chunk in piece.chunks:
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the phone skipped ahead or closed the player
        finally:
            close = getattr(piece.chunks, "close", None)
            if close:
                close()

    # -- audio -------------------------------------------------------------

    def _stream(self, video_id: str) -> None:
        streaming = self.app.streaming
        try:
            path = streaming.cached(video_id)
            if path:
                return self._send_file(path)
            job = streaming.job(video_id)
            # A resume/seek needs the complete file; give the conversion a moment.
            if re.match(r"bytes=[1-9]", self.headers.get("Range") or ""):
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

    def _live(self, video_id: str) -> None:
        """A running livestream: endless MP3, converted while it is listened to."""
        if self.command == "HEAD":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            return
        try:
            chunks = self.app.streaming.live(video_id)
            first = next(chunks, None)
        except Exception as exc:  # noqa: BLE001 - e.g. the stream has ended
            log.error("livestream %s failed: %s", video_id, exc)
            return self._error(502, str(exc))
        if first is None:
            return self._error(502, "Der Livestream liefert keinen Ton.")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Accept-Ranges", "none")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(first)
            for chunk in chunks:
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # speaker stopped listening
        finally:
            chunks.close()  # stops ffmpeg

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


def make_server(app: Application, host: str = "0.0.0.0", port: int = 8327,
                allowed_hosts: list[str] | None = None, public_url: str | None = None) -> ThreadingHTTPServer:
    hosts = {h.strip().lower() for h in allowed_hosts or [] if h.strip()}
    if public_url:
        hosts.add((urlsplit(public_url).hostname or "").lower())
    handler = type("BoundHandler", (Handler,), {
        "app": app, "gets": get_routes(app), "posts": post_routes(app), "allowed_hosts": frozenset(hosts),
    })
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    app.playback.port = server.server_address[1]  # the stream URLs need the real port (port 0 in tests)
    return server
