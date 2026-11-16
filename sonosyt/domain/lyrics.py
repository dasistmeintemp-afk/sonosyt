"""Finding the right lyrics for a YouTube video.

YouTube titles are messy ("Artist - Song (Official Video) [4K]"), so the title
is cleaned and split into artist and song before searching. Where a result has
timestamps, the lyrics can follow the music line by line.
"""

from __future__ import annotations

import re

NOISE = re.compile(
    r"\s*[\(\[\{][^\)\]\}]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|"
    r"remaster(ed)?|music video|clip|mv|explicit|color coded)\b[^\)\]\}]*[\)\]\}]",
    re.I,
)
LINE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$")


def split_title(title: str, uploader: str = "") -> tuple[str, str]:
    """(artist, song) guessed from a YouTube title and channel name."""
    clean = NOISE.sub("", title)
    clean = re.sub(r"\s*\|.*$", "", clean)  # "Song | Live at ..." -> "Song"
    clean = re.sub(r"\s+", " ", clean).strip(" -–—")
    parts = re.split(r"\s+[-–—]\s+", clean, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    artist = re.sub(r"\s*(- Topic|VEVO|Official)$", "", uploader or "", flags=re.I).strip()
    return artist, clean


def parse_synced(text: str) -> list[tuple[float, str]]:
    lines = []
    for raw in (text or "").splitlines():
        m = LINE.match(raw.strip())
        if m:
            lines.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
    return lines


def best_match(results: list[dict], duration: int | None) -> dict | None:
    def score(item: dict) -> tuple:
        off = abs((item.get("duration") or 0) - duration) if duration else 0
        return (
            bool(item.get("instrumental")),  # instrumentals have nothing to show
            off > 10 if duration else False,  # clearly another recording
            not item.get("syncedLyrics"),
            off,
        )

    usable = [r for r in results if r.get("syncedLyrics") or r.get("plainLyrics") or r.get("instrumental")]
    return min(usable, key=score) if usable else None


def searches(artist: str, song: str) -> list[dict]:
    """Queries to try, most precise first."""
    attempts = [{"track_name": song, "artist_name": artist}] if artist else []
    return attempts + [{"q": f"{artist} {song}".strip()}]


def answer(result: dict | None, artist: str, song: str) -> dict:
    """{"found", "synced": [[seconds, line], ...], "plain", "artist", "song"}."""
    if not result:
        return {"found": False, "synced": [], "plain": "", "artist": artist, "song": song}
    if result.get("instrumental"):
        return {"found": True, "instrumental": True, "synced": [], "plain": "",
                "artist": artist, "song": song}
    return {
        "found": True,
        "synced": [[round(t, 2), line] for t, line in parse_synced(result.get("syncedLyrics") or "")],
        "plain": (result.get("plainLyrics") or "").strip(),
        "artist": result.get("artistName") or artist,
        "song": result.get("trackName") or song,
    }
