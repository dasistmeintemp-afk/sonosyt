"""Rules for playing: what a search plays, when to resume, what comes next."""

from __future__ import annotations

import re

from .track import Track, is_url

PLAY_MODES = ("now", "next", "queue")
LONG_VIDEO = 1200  # seconds; longer videos are resumed where they were left
PREFETCH_MAX = 1200  # search hits up to this length are converted speculatively


def tracks_to_play(found: list[Track], query: str) -> list[Track]:
    """A search term means "the best hit"; a playlist link means all of it."""
    return found if is_url(query) else found[:1]


def likely_picks(found: list[Track], count: int = 3) -> list[str]:
    """Search hits worth converting before anyone taps them."""
    return [t.id for t in found if not t.live and t.duration and t.duration <= PREFETCH_MAX][:count]


def playback_error(detail: str) -> str:
    """One readable line from the converter's error output."""
    lines = [line.strip() for line in (detail or "").splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    line = (errors or lines or ["unbekannter Fehler"])[0]
    line = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?([A-Za-z0-9_-]{11}:\s*)?", "", line)
    return f"Dieses Video lässt sich nicht abspielen: {line[:200]}"


def position_to_remember(status: dict) -> tuple[str, int] | None:
    """(video id, seconds) to store for a long video, 0 seconds once it is finished."""
    video_id, duration = status.get("video_id"), status.get("duration") or 0
    if not video_id or status.get("live") or duration < LONG_VIDEO or status.get("state") != "PLAYING":
        return None
    position = status.get("position") or 0
    if position >= duration - 60:
        return video_id, 0
    if position > 30:
        return video_id, position
    return None


def wants_autoplay(status: dict, last_handled: str | None) -> bool:
    """True while the last queued track plays and nothing has been added for it yet."""
    video_id = status.get("video_id")
    return bool(
        status.get("state") == "PLAYING"
        and video_id
        and not status.get("live")
        and not status.get("repeat")
        and status.get("queue_position") == status.get("queue_length")
        and last_handled != video_id
    )


def pick_autoplay(candidates: list[Track], recently_played: set[str]) -> Track | None:
    """Prefer something not heard lately; never a livestream."""
    usable = [t for t in candidates if not t.live]
    fresh = [t for t in usable if t.id not in recently_played]
    return (fresh or usable or [None])[0]


def upcoming_video(status: dict, queue: list[dict]) -> str | None:
    """The video after the one playing, if it is ours and can be converted."""
    position, length = status.get("queue_position") or 0, status.get("queue_length") or 0
    if status.get("state") != "PLAYING" or not 0 < position < length or position >= len(queue):
        return None
    upcoming = queue[position]  # 0-based index of the item after the current one
    if not upcoming.get("video_id") or upcoming.get("live"):
        return None
    return upcoming["video_id"]
