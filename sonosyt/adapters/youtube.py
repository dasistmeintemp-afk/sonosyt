"""YouTube via yt-dlp: search, single videos, playlists, related videos, covers.

Implements the VideoCatalog port.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from ..domain.errors import MediaError
from ..domain.track import VIDEO_ID_RE, Track, watch_id, watch_url

def _track_from_info(info: dict) -> Track | None:
    video_id = info.get("id") or ""
    if not VIDEO_ID_RE.match(video_id):
        return None  # channels, playlists-in-playlists, non-YouTube results
    duration = info.get("duration")
    live = bool(info.get("is_live")) or info.get("live_status") == "is_live"
    return Track(
        id=video_id,
        title=info.get("title") or video_id,
        uploader=info.get("channel") or info.get("uploader") or "",
        duration=int(duration) if duration and not live else None,
        live=live,
    )


def _extract(target: str, playlist: bool = False) -> dict:
    """Run yt-dlp for metadata only.

    As a subprocess rather than an import, so that a yt-dlp upgrade installed
    while the server runs is picked up by the very next request.
    """
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-single-json", "--flat-playlist", "--no-warnings", "--skip-download",
        # A watch URL that also carries &list=... normally means "this video".
        "--yes-playlist" if playlist else "--no-playlist",
        "--", target,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("YouTube antwortet nicht (Zeitüberschreitung).") from exc
    if proc.returncode != 0:
        message = (proc.stderr or "").strip().splitlines()
        raise MediaError(message[-1] if message else "yt-dlp ist fehlgeschlagen.")
    try:
        return json.loads(proc.stdout) or {}
    except ValueError as exc:
        raise MediaError("Unerwartete Antwort von yt-dlp.") from exc


def _tracks(info: dict) -> list[Track]:
    entries = info.get("entries")
    infos = list(entries) if entries is not None else [info]
    return [t for t in (_track_from_info(i) for i in infos if i) if t]


def lookup(query: str, limit: int = 10) -> list[Track]:
    """Resolve a video URL, playlist URL, bare video id or search term."""
    query = query.strip()
    if not query:
        return []
    if VIDEO_ID_RE.match(query):
        target = watch_url(query)
    elif re.match(r"^https?://", query, re.I):
        if not re.match(
            r"^https?://([\w-]+\.)*(youtube\.com|youtu\.be|youtube-nocookie\.com)(/|$)",
            query,
            re.I,
        ):
            raise MediaError("Nur YouTube-URLs werden unterstützt.")
        target = query
    else:
        target = f"ytsearch{int(limit)}:{query}"
    return _tracks(_extract(target))


def related(video_id: str) -> list[Track]:
    """Videos YouTube would play after this one (its auto-generated mix)."""
    mix = f"{watch_url(video_id)}&list=RD{video_id}"
    return [t for t in _tracks(_extract(mix, playlist=True)) if t.id != video_id]


def suggest(query: str, limit: int = 8) -> list[str]:
    """Search suggestions as YouTube shows them while typing. Empty on any failure."""
    query = query.strip()
    if not query:
        return []
    url = "https://suggestqueries.google.com/complete/search?" + urllib.parse.urlencode(
        {"client": "firefox", "ds": "yt", "ie": "utf-8", "oe": "utf-8", "q": query}
    )
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return [str(item) for item in data[1] if isinstance(item, str)][:limit]
    except Exception:  # noqa: BLE001 - suggestions are a convenience only
        return []


def thumbnail(video_id: str) -> bytes:
    """The small YouTube cover of a video (320x180 JPEG)."""
    url = f"https://i.ytimg.com/vi/{watch_id(video_id)}/mqdefault.jpg"
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = resp.read(512 * 1024)
    except OSError as exc:
        raise MediaError(f"Kein Cover für {video_id}") from exc
    if not data.startswith(b"\xff\xd8"):
        raise MediaError(f"Kein Cover für {video_id}")
    return data


class YtDlpCatalog:
    """The VideoCatalog port. Looks the functions up at call time, so they can be
    replaced in tests."""

    def lookup(self, query: str, limit: int = 10) -> list[Track]:
        return lookup(query, limit)

    def related(self, video_id: str) -> list[Track]:
        return related(video_id)

    def suggest(self, query: str) -> list[str]:
        return suggest(query)

    def thumbnail(self, video_id: str) -> bytes:
        return thumbnail(video_id)
