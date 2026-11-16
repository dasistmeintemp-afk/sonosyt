"""Song lyrics from LRCLIB (lrclib.net), a free, open lyrics database.

YouTube titles are messy ("Artist - Song (Official Video) [4K]"), so the title
is cleaned and split into artist and song before searching. Where a result has
timestamps, the lyrics can follow the music line by line.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request

from .youtube import Track

API = "https://lrclib.net/api/search?"
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


def _search(params: dict) -> list[dict]:
    req = urllib.request.Request(
        API + urllib.parse.urlencode(params), headers={"User-Agent": "sonosyt/0.1"}
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return data if isinstance(data, list) else []


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


class Lyrics:
    def __init__(self, search=_search):
        self.search = search
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, track: Track) -> dict:
        """{"found", "synced": [[seconds, line], ...], "plain", "source"}."""
        with self._lock:
            if track.id in self._cache:
                return self._cache[track.id]
        artist, song = split_title(track.title, track.uploader)
        result = None
        attempts = []
        if artist:
            attempts.append({"track_name": song, "artist_name": artist})
        attempts.append({"q": f"{artist} {song}".strip()})
        for params in attempts:
            try:
                result = best_match(self.search(params), track.duration)
            except Exception:  # noqa: BLE001 - offline or service down: just no lyrics
                result = None
            if result:
                break
        if not result:
            answer = {"found": False, "synced": [], "plain": "", "artist": artist, "song": song}
        elif result.get("instrumental"):
            answer = {"found": True, "instrumental": True, "synced": [], "plain": "",
                      "artist": artist, "song": song}
        else:
            answer = {
                "found": True,
                "synced": [[round(t, 2), line] for t, line in parse_synced(result.get("syncedLyrics") or "")],
                "plain": (result.get("plainLyrics") or "").strip(),
                "artist": result.get("artistName") or artist,
                "song": result.get("trackName") or song,
            }
        with self._lock:
            self._cache[track.id] = answer
            while len(self._cache) > 300:
                self._cache.pop(next(iter(self._cache)))
        return answer
