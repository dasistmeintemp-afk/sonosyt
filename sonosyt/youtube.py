"""YouTube metadata lookup (search, single videos, playlists) via yt-dlp."""

from __future__ import annotations

import re
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

    @property
    def thumbnail(self) -> str:
        return f"https://i.ytimg.com/vi/{self.id}/hqdefault.jpg"

    @property
    def watch_url(self) -> str:
        return watch_url(self.id)

    def to_dict(self) -> dict:
        return {**asdict(self), "thumbnail": self.thumbnail}


def watch_url(video_id: str) -> str:
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError(f"invalid YouTube video id: {video_id!r}")
    return f"https://www.youtube.com/watch?v={video_id}"


def _track_from_info(info: dict) -> Track | None:
    video_id = info.get("id") or ""
    if not VIDEO_ID_RE.match(video_id):
        return None  # channels, playlists-in-playlists, non-YouTube results
    duration = info.get("duration")
    return Track(
        id=video_id,
        title=info.get("title") or video_id,
        uploader=info.get("channel") or info.get("uploader") or "",
        duration=int(duration) if duration else None,
    )


def _extract(target: str) -> dict:
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        # A watch URL that also carries &list=... means "this video".
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False) or {}
    except yt_dlp.utils.DownloadError as exc:
        raise YouTubeError(str(exc)) from exc


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

    info = _extract(target)
    entries = info.get("entries")
    infos = list(entries) if entries is not None else [info]
    tracks = [t for t in (_track_from_info(i) for i in infos if i) if t]
    return tracks
