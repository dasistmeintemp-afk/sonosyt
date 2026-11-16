"""A YouTube video as something to listen to."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def watch_id(video_id: str) -> str:
    """The id itself, if it is a valid YouTube video id; ValueError otherwise."""
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError(f"invalid YouTube video id: {video_id!r}")
    return video_id


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={watch_id(video_id)}"


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", (text or "").strip(), re.I))


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
