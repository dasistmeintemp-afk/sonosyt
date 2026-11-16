"""HTTP server: audio streams for the speakers, JSON API and web UI for people."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import shutil
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__, youtube
from .library import Library
from .lyrics import Lyrics
from .sonos import SonosController, SonosError, local_ip_for
from .transcode import Transcoder
from .youtube import Track, YouTubeError

log = logging.getLogger(__name__)

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


def playback_error(detail: str) -> str:
    """One readable line from yt-dlp/ffmpeg output."""
    lines = [line.strip() for line in (detail or "").splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    line = (errors or lines or ["unbekannter Fehler"])[0]
    line = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?([A-Za-z0-9_-]{11}:\s*)?", "", line)
    return f"Dieses Video lässt sich nicht abspielen: {line[:200]}"


THUMB_RE = re.compile(r"^/thumb/([A-Za-z0-9_-]{11})\.jpg$")
THUMB_LIMIT = 3000  # cached cover images kept on disk (~15 kB each)


def fetch_thumbnail(video_id: str) -> bytes:
    """The small YouTube cover of a video (320x180 JPEG)."""
    import urllib.request

    url = f"https://i.ytimg.com/vi/{youtube.watch_id(video_id)}/mqdefault.jpg"
    with urllib.request.urlopen(url, timeout=6) as resp:
        data = resp.read(512 * 1024)
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG")
    return data


STREAM_RE = re.compile(r"^/(stream|live)/([A-Za-z0-9_-]{11})\.mp3$")
AUTOPLAY_INTERVAL = 8.0
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
        library: Library | None = None,
        related=youtube.related,
        suggest=youtube.suggest,
        allowed_hosts: list[str] | None = None,
    ):
        self.suggest = suggest
        self.fetch_thumbnail = fetch_thumbnail
        self.lyrics = Lyrics()
        self._alarm_fired: dict[str, str] = {}  # alarm id -> "date time" it last went off
        hosts = {h.strip().lower() for h in allowed_hosts or [] if h.strip()}
        if public_url:
            hosts.add((urlsplit(public_url).hostname or "").lower())
        self.allowed_hosts = frozenset(hosts)
        self.transcoder = transcoder
        self.sonos = sonos
        self.port = port
        self.public_url = public_url.rstrip("/") if public_url else None
        self.lookup = lookup
        self.related = related
        self.prefetch_wait = prefetch_wait
        self.library = library or Library(transcoder.cache_dir / "library.json")
        self._tracks: dict[str, Track] = {}
        self._lock = threading.Lock()
        self._extended: dict[str, str] = {}  # speaker -> video autoplay last handled
        self._active: dict[str, float] = {}  # speaker -> when it was last played to
        self._next_checked: dict[str, tuple] = {}
        self._ytdlp: tuple[float, str] | None = None
        self.fetches: dict[str, float] = {}  # client address -> last audio request
        self.remember(self.library.tracks())

    # -- tracks --------------------------------------------------------------

    def remember(self, tracks: list[Track]) -> None:
        with self._lock:
            for t in tracks:
                self._tracks[t.id] = t
            while len(self._tracks) > 2000:
                self._tracks.pop(next(iter(self._tracks)))

    def search(self, query: str, prefetch: bool = False, limit: int = 10) -> list[Track]:
        tracks = self.lookup(query, limit) if limit != 10 else self.lookup(query)
        self.remember(tracks)
        if prefetch:
            # Convert the most likely picks already, so a tap starts at once.
            likely = [t.id for t in tracks if not t.live and t.duration and t.duration <= 1200]
            self.transcoder.prefetch(likely[:3])
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

    def stream_url(self, track: Track, speaker_ip: str) -> str:
        base = self.public_url or f"http://{local_ip_for(speaker_ip)}:{self.port}"
        return f"{base}/{'live' if track.live else 'stream'}/{track.id}.mp3"

    # -- playing -------------------------------------------------------------

    def play(self, speaker: str, ids: list[str] | None, query: str | None, mode: str = "now") -> dict:
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
        if mode == "now" and not tracks[0].live:
            # Short videos finish converting within seconds; then Sonos gets a
            # complete file (with duration and seeking) instead of a live stream.
            job = self.transcoder.ensure(tracks[0].id)
            job.done.wait(self.prefetch_wait)
            if job.done.is_set() and job.error:  # e.g. removed, private or age-restricted
                raise YouTubeError(playback_error(job.error))
        # The next few are converted ahead; later ones follow as playback gets there.
        self.transcoder.prefetch([t.id for t in tracks if not t.live][:4])
        items = [(t, self.stream_url(t, device.ip_address)) for t in tracks]
        position = self.sonos.enqueue(speaker, items, mode)
        self.library.played(tracks)
        self._active[speaker] = time.monotonic()
        resumed = self._resume(speaker, tracks[0]) if mode == "now" and len(tracks) == 1 else 0
        return {"queued": [t.to_dict() for t in tracks], "position": position, "resumed": resumed}

    def _resume(self, speaker: str, track: Track) -> int:
        """Continue a long video where it was left. Returns the position, or 0."""
        seconds = self.library.position(track.id)
        if not seconds or track.live or not self.transcoder.cached(track.id):
            return 0  # seeking needs the complete file
        for _ in range(3):  # the speaker may still be switching tracks
            try:
                self.sonos.control(speaker, "seek", seconds)
                return seconds
            except Exception:  # noqa: BLE001
                time.sleep(1)
        return 0

    def remember_position(self, status: dict) -> None:
        """Note where a long video is, so it can be continued later."""
        video_id, duration = status.get("video_id"), status.get("duration") or 0
        if not video_id or status.get("live") or duration < 1200 or status.get("state") != "PLAYING":
            return
        position = status.get("position") or 0
        if position >= duration - 60:
            self.library.set_position(video_id, 0)  # finished
        elif position > 30:
            self.library.set_position(video_id, position)

    def diagnose(self, speaker: str) -> dict:
        """Checks for the settings screen: who can reach whom."""
        import urllib.request

        device = self.sonos.get(speaker)
        try:
            with urllib.request.urlopen("https://www.youtube.com/generate_204", timeout=6):
                youtube_ok = True
        except Exception:  # noqa: BLE001
            youtube_ok = False
        fetched = self.fetches.get(device.ip_address)
        if fetched is None:  # a stereo pair or group may fetch through another member
            fetched = max(self.fetches.values(), default=None)
        return {
            "youtube": youtube_ok,
            "speaker": self.sonos.reachable(speaker),
            "fetched_ago": None if fetched is None else int(time.time() - fetched),
            "stream_base": self.stream_url(Track("xxxxxxxxxxx", ""), device.ip_address)
            .rsplit("/stream/", 1)[0],
        }

    def status(self, speaker: str) -> dict:
        status = self.sonos.status(speaker)
        status["autoplay"] = self.library.autoplay(speaker)
        return status

    def info(self) -> dict:
        """Facts for the settings screen."""
        now = time.monotonic()
        if not self._ytdlp or now - self._ytdlp[0] > 600:
            from .updater import ytdlp_version

            self._ytdlp = (now, ytdlp_version())
        return {"version": __version__, "ytdlp": self._ytdlp[1],
                "cache": self.transcoder.cache_info()}

    # -- autoplay ------------------------------------------------------------

    def autoplay_once(self, speaker: str, status: dict | None = None) -> Track | None:
        """If the last queued track is playing, queue what YouTube would play next."""
        status = status or self.sonos.status(speaker)
        video_id = status.get("video_id")
        if (
            status.get("state") != "PLAYING"
            or not video_id
            or status.get("live")
            or status.get("repeat")
            or status.get("queue_position") != status.get("queue_length")
            or self._extended.get(speaker) == video_id  # one attempt per track
        ):
            return None
        self._extended[speaker] = video_id
        recent = self.library.recent_ids() | {video_id}
        candidates = [t for t in self.related(video_id) if not t.live]
        fresh = [t for t in candidates if t.id not in recent]
        choice = (fresh or candidates or [None])[0]
        if choice is None:
            return None
        self.remember([choice])
        device = self.sonos.get(speaker)
        self.transcoder.ensure(choice.id)  # convert ahead, so it starts seamlessly
        self.sonos.enqueue(
            speaker, [(choice, self.stream_url(choice, device.ip_address))], "queue"
        )
        self.library.played([choice])
        log.info("autoplay in %s: %s", speaker, choice.title)
        return choice

    def prefetch_next(self, speaker: str, status: dict) -> str | None:
        """Convert the track after the playing one, so it starts without a gap."""
        position, length = status.get("queue_position") or 0, status.get("queue_length") or 0
        key = (status.get("video_id"), position, length)
        if (
            status.get("state") != "PLAYING"
            or not 0 < position < length
            or self._next_checked.get(speaker) == key
        ):
            return None
        self._next_checked[speaker] = key
        queue = self.sonos.queue(speaker)
        if position >= len(queue):
            return None
        upcoming = queue[position]  # 0-based index of the item after the current one
        if not upcoming.get("video_id") or upcoming.get("live"):
            return None
        self.transcoder.prefetch([upcoming["video_id"]])
        return upcoming["video_id"]

    # -- alarms ------------------------------------------------------------------

    def due_alarms(self, now=None) -> list[dict]:
        """Alarms whose minute has come and that have not gone off for it yet."""
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        now = now or datetime.now(timezone.utc)
        due = []
        for alarm in self.library.alarms():
            if not alarm["enabled"]:
                continue
            local = now.astimezone(ZoneInfo(alarm["tz"]))
            key = f"{local:%Y-%m-%d} {alarm['time']}"
            if local.strftime("%H:%M") != alarm["time"] or self._alarm_fired.get(alarm["id"]) == key:
                continue
            if alarm["days"] and local.weekday() not in alarm["days"]:
                continue
            self._alarm_fired[alarm["id"]] = key
            due.append(alarm)
        return due

    def fire_alarm(self, alarm: dict, ramp_seconds: float = 90.0) -> str:
        """Start what an alarm plays. Returns a short description."""
        import random

        speaker, source, target = alarm["speaker"], alarm["source"], alarm["volume"]
        start_volume = min(3, target) if alarm["fade"] else target
        self.sonos.set_volume(speaker, start_volume)
        if source["type"] == "sonosfav":
            what = self.sonos.play_favorite(speaker, source["index"])
        else:
            if source["type"] == "playlist":
                tracks = next((p["tracks"] for p in self.library.snapshot()["playlists"]
                               if p["name"] == source["name"]), None)
                if tracks is None:
                    raise YouTubeError(f"Die Playlist „{source['name']}“ gibt es nicht mehr.")
                what = source["name"]
            else:
                tracks, what = self.library.snapshot()["favorites"], "Favoriten"
            ids = [t["id"] for t in tracks if not t.get("live")]
            if not ids:
                raise YouTubeError(f"„{what}“ ist leer.")
            if alarm["shuffle"]:
                random.shuffle(ids)
            self.play(speaker, ids[:100], None, "now")
        if alarm["fade"] and target > start_volume:
            def ramp():
                steps = 15
                for i in range(1, steps + 1):
                    time.sleep(ramp_seconds / steps)
                    try:
                        self.sonos.set_volume(speaker, start_volume + (target - start_volume) * i // steps)
                    except Exception:  # noqa: BLE001 - speaker busy: try the next step
                        continue
            threading.Thread(target=ramp, name="alarm-fade", daemon=True).start()
        if not alarm["days"]:
            self.library.disable_alarm(alarm["id"])  # a one-off alarm is done
        log.info("alarm %s in %s: %s", alarm["time"], speaker, what)
        return what

    def check_alarms(self) -> None:
        for alarm in self.due_alarms():
            def run(alarm=alarm):
                try:
                    self.fire_alarm(alarm)
                except Exception as exc:  # noqa: BLE001
                    log.warning("alarm %s failed: %s", alarm["time"], exc)
            threading.Thread(target=run, name="alarm", daemon=True).start()

    def monitor_once(self) -> None:
        """One round of background care for the rooms in use."""
        try:
            self.check_alarms()
        except Exception as exc:  # noqa: BLE001
            log.warning("alarm check failed: %s", exc)
        now = time.monotonic()
        self._active = {s: t for s, t in self._active.items() if now - t < 12 * 3600}
        autoplay = set(self.library.autoplay_speakers())
        for speaker in sorted(autoplay | set(self._active)):
            try:
                status = self.sonos.status(speaker)
                self.remember_position(status)
                self.prefetch_next(speaker, status)
                if speaker in autoplay:
                    self.autoplay_once(speaker, status)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("monitoring %s failed: %s", speaker, exc)

    def start_autoplay(self) -> threading.Thread:
        def loop() -> None:
            while True:
                self.monitor_once()
                time.sleep(AUTOPLAY_INTERVAL)

        thread = threading.Thread(target=loop, name="monitor", daemon=True)
        thread.start()
        return thread


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
            if isinstance(exc, OSError) or exc.__class__.__module__.startswith(("soco", "requests")):
                # A speaker that is off or busy is everyday business, not a crash.
                log.warning("%s failed: %s: %s", self.path, exc.__class__.__name__, exc)
            else:
                log.exception("request failed")
            self._error(500, f"{exc.__class__.__name__}: {exc}")

    # -- routing -----------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def _refuse_foreign_host(self) -> bool:
        if host_allowed(self.headers.get("Host"), self.app.allowed_hosts):
            return False
        self._error(403, "Dieser Hostname ist nicht freigegeben. Den Server über seine IP-Adresse "
                         "aufrufen oder mit --allow-host <name> starten.")
        return True

    def do_GET(self):
        if self._refuse_foreign_host():
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        m = THUMB_RE.match(url.path)
        if m:
            return self._thumb(m.group(1))
        m = STREAM_RE.match(url.path)
        if m:
            self.app.fetches[self.client_address[0]] = time.time()
            return self._live(m.group(2)) if m.group(1) == "live" else self._stream(m.group(2))
        if url.path in ("/", "/index.html"):
            return self._index()
        if url.path == "/api/ping":  # lets the Android app recognise the server
            return self._json({"app": "sonosyt", "version": __version__})
        if url.path == "/api/speakers":
            refresh = query.get("refresh", ["0"])[0] == "1"
            return self._guard(lambda: self.app.sonos.speakers(refresh))
        if url.path == "/api/search":
            q = query.get("q", [""])[0]
            limit = max(1, min(30, int(query.get("limit", ["10"])[0] or 10)))
            return self._guard(
                lambda: [t.to_dict() for t in self.app.search(q, prefetch=True, limit=limit)]
            )
        if url.path == "/api/diagnose":
            speaker = query.get("speaker", [""])[0]
            return self._guard(lambda: self.app.diagnose(speaker))
        if url.path == "/api/related":
            def related():
                tracks = self.app.related(youtube.watch_id(query.get("id", [""])[0]))
                self.app.remember(tracks)
                return [t.to_dict() for t in tracks]
            return self._guard(related)
        if url.path == "/api/suggest":
            q = query.get("q", [""])[0]
            return self._guard(lambda: self.app.suggest(q[:200]))
        if url.path == "/api/queue":
            speaker = query.get("speaker", [""])[0]
            return self._guard(lambda: self.app.sonos.queue(speaker))
        if url.path == "/api/status":
            speaker = query.get("speaker", [""])[0]
            return self._guard(lambda: self.app.status(speaker))
        if url.path == "/api/rooms":
            speaker = query.get("speaker", [""])[0]
            return self._guard(lambda: self.app.sonos.rooms(speaker))
        if url.path == "/api/info":
            return self._guard(self.app.info)
        if url.path == "/api/alarms":
            return self._guard(self.app.library.alarms)
        if url.path == "/api/sonosfav":
            speaker = query.get("speaker", [""])[0]
            return self._guard(lambda: self.app.sonos.favorites(speaker))
        if url.path == "/api/lyrics":
            def lyrics():
                return self.app.lyrics.get(self.app.track(youtube.watch_id(query.get("id", [""])[0])))
            return self._guard(lyrics)
        if url.path == "/api/library":
            return self._guard(self.app.library.snapshot)
        self._error(404, "not found")

    def do_POST(self):
        if self._refuse_foreign_host():
            return
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
                mode = body.get("mode") or ("queue" if body.get("queue") else "now")
                if mode not in ("now", "next", "queue"):
                    raise ValueError("mode must be now, next or queue")
                return self.app.play(str(body["speaker"]), ids, body.get("query"), mode)
            return self._guard(play)
        if path == "/api/control":
            def control():
                body = self._body()
                speaker = str(body["speaker"])
                if "volume" in body:
                    self.app.sonos.set_volume(
                        speaker, int(body["volume"]), bool(body.get("single"))
                    )
                if body.get("action"):
                    value, to = body.get("value"), body.get("to")
                    self.app.sonos.control(
                        speaker,
                        str(body["action"]),
                        None if value is None else int(value),
                        None if to is None else int(to),
                    )
                return {"ok": True}
            return self._guard(control)
        if path == "/api/favorite":
            def favorite():
                body = self._body()
                track = self.app.track(youtube.watch_id(str(body["id"])))
                self.app.library.set_favorite(track, bool(body.get("favorite", True)))
                return self.app.library.snapshot()
            return self._guard(favorite)
        if path == "/api/cache/clear":
            def clear_cache():
                self._body()
                removed = self.app.transcoder.clear_cache()
                return {"removed": removed, **self.app.info()}
            return self._guard(clear_cache)
        if path == "/api/history/clear":
            def clear_history():
                self._body()
                self.app.library.clear_history()
                return self.app.library.snapshot()
            return self._guard(clear_history)
        if path == "/api/playlist":
            def playlist():
                body = self._body()
                name, op = str(body["name"]), str(body.get("op") or "add")
                if op == "add":
                    track = self.app.track(youtube.watch_id(str(body["id"])))
                    self.app.library.playlist_add(name, track)
                elif op == "remove":
                    self.app.library.playlist_remove(name, str(body["id"]))
                elif op == "delete":
                    self.app.library.playlist_delete(name)
                elif op == "set":
                    ids = body.get("ids")
                    if not isinstance(ids, list) or not ids:
                        raise ValueError("ids must be a non-empty list")
                    tracks = [self.app.track(youtube.watch_id(str(i))) for i in ids[:500]]
                    self.app.library.playlist_set(name, tracks)
                else:
                    raise ValueError("op must be add, remove, delete or set")
                return self.app.library.snapshot()
            return self._guard(playlist)
        if path == "/api/autoplay":
            def autoplay():
                body = self._body()
                speaker = str(body["speaker"])
                self.app.sonos.get(speaker)  # must exist
                self.app.library.set_autoplay(speaker, bool(body.get("on")))
                return {"autoplay": self.app.library.autoplay(speaker)}
            return self._guard(autoplay)
        if path == "/api/sonosfav/play":
            def play_favorite():
                body = self._body()
                speaker = str(body["speaker"])
                title = self.app.sonos.play_favorite(speaker, int(body["index"]))
                self.app._active[speaker] = time.monotonic()
                return {"title": title}
            return self._guard(play_favorite)
        if path == "/api/alarms":
            def alarms():
                body = self._body()
                op = body.get("op") or "save"
                if op == "save":
                    alarm = dict(body["alarm"])
                    self.app.sonos.get(str(alarm.get("speaker") or ""))  # the room must exist
                    self.app.library.save_alarm(alarm)
                elif op == "delete":
                    self.app.library.delete_alarm(str(body["id"]))
                elif op == "test":
                    alarm = next((a for a in self.app.library.alarms() if a["id"] == body["id"]), None)
                    if alarm is None:
                        raise ValueError("Diesen Wecker gibt es nicht.")
                    return {"playing": self.app.fire_alarm({**alarm, "days": [0]}, ramp_seconds=20)}
                else:
                    raise ValueError("op must be save, delete or test")
                return self.app.library.alarms()
            return self._guard(alarms)
        if path == "/api/eq":
            def eq():
                body = self._body()
                return self.app.sonos.set_eq(
                    str(body["room"]), body.get("bass"), body.get("treble"), body.get("loudness")
                )
            return self._guard(eq)
        if path == "/api/group":
            def group():
                body = self._body()
                speaker = str(body["speaker"])
                self.app.sonos.group(speaker, str(body["room"]), bool(body.get("join")))
                return self.app.sonos.rooms(speaker)
            return self._guard(group)
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

    def _live(self, video_id: str) -> None:
        """A running livestream: endless MP3, converted while it is listened to."""
        if self.command == "HEAD":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            return
        try:
            chunks = self.app.transcoder.live(video_id)
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

    def _thumb(self, video_id: str) -> None:
        """Cover images from our own origin, so the page may read their colours."""
        folder = self.app.transcoder.cache_dir / "thumbs"
        path = folder / f"{video_id}.jpg"
        try:
            data = path.read_bytes()
        except OSError:
            try:
                data = self.app.fetch_thumbnail(video_id)
            except Exception:  # noqa: BLE001 - no cover: the page shows a placeholder
                return self._error(404, "kein Cover")
            try:
                folder.mkdir(exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
                files = sorted(folder.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
                for old in files[: max(0, len(files) - THUMB_LIMIT)]:
                    old.unlink(missing_ok=True)
            except OSError:
                pass  # serving still works without the cache
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

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
