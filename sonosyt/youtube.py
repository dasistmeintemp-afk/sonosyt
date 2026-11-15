"""YouTube metadata lookup (search, single videos, playlists) via yt-dlp."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YouTubeError(Exception):
    """YouTube could not be queried or returned nothing usable."""


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    uploader: str = ""
    duration: int | None = None  # seconds
    live: bool = False  # a running livestream: endless, cannot be cached

    @property
    def thumbnail(self) -> str:
        return f"https://i.ytimg.com/vi/{self.id}/hqdefault.jpg"

    def to_dict(self) -> dict:
        return {**asdict(self), "thumbnail": self.thumbnail}

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        duration = data.get("duration")
        return cls(
            id=watch_id(str(data["id"])),
            title=str(data.get("title") or data["id"]),
            uploader=str(data.get("uploader") or ""),
            duration=int(duration) if duration else None,
            live=bool(data.get("live")),
        )


def watch_id(video_id: str) -> str:
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError(f"invalid YouTube video id: {video_id!r}")
    return video_id


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={watch_id(video_id)}"


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
        raise YouTubeError("YouTube antwortet nicht (Zeitüberschreitung).") from exc
    if proc.returncode != 0:
        message = (proc.stderr or "").strip().splitlines()
        raise YouTubeError(message[-1] if message else "yt-dlp ist fehlgeschlagen.")
    try:
        return json.loads(proc.stdout) or {}
    except ValueError as exc:
        raise YouTubeError("Unerwartete Antwort von yt-dlp.") from exc


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
            raise YouTubeError("Nur YouTube-URLs werden unterstützt.")
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
