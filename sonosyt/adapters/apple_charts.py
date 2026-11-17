"""Current hit lists from Apple's public chart feed (no account needed).

Implements the ChartSource port. YouTube retired its own trending page in
2025, and its charts have no public interface; Apple's "most played" feed is
updated daily and lists artist and title, which is all sonosyt needs to find
the songs on YouTube.
"""

from __future__ import annotations

import json
import re
import urllib.request

from ..domain.errors import MediaError

URL = "https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/{limit}/songs.json"


class AppleCharts:
    def __init__(self, opener=None):
        self.opener = opener or (lambda url: urllib.request.urlopen(url, timeout=10))

    def top_songs(self, country: str, limit: int) -> list[dict]:
        if not re.fullmatch(r"[a-z]{2}", country):
            raise ValueError("Ländercode mit zwei Buchstaben erwartet, z. B. de")
        try:
            with self.opener(URL.format(country=country, limit=int(limit))) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except (OSError, ValueError) as exc:
            raise MediaError(f"Charts nicht erreichbar: {exc}") from exc
        songs = []
        for entry in (data.get("feed") or {}).get("results") or []:
            title, artist = entry.get("name"), entry.get("artistName")
            if title and artist:
                art = str(entry.get("artworkUrl100") or "").replace("100x100", "300x300")
                songs.append({"title": str(title), "artist": str(artist),
                              "art": art if art.startswith("https://") else ""})
        if not songs:
            raise MediaError("Die Charts sind gerade leer.")
        return songs
