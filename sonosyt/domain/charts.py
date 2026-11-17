"""Rules for turning a hit list (artist and title) into YouTube videos."""

from __future__ import annotations

import re

from .music import by_artist, looks_like_song
from .track import Track

COUNTRIES = {"de": "Deutschland", "at": "Österreich", "ch": "Schweiz", "us": "USA", "gb": "Großbritannien",
             "fr": "Frankreich", "it": "Italien", "es": "Spanien", "nl": "Niederlande"}
# besides what is no song at all: versions that are not the hit itself
OTHER_VERSION = re.compile(r"\b(mix|cover|sped up|slowed|nightcore|8d)\b", re.I)


def chart_title(country: str) -> str:
    return "Top-Songs " + COUNTRIES.get(country.lower(), country.upper())


def chart_query(artist: str, title: str) -> str:
    # "(feat. X)" and "- Remastered" make YouTube's search worse, not better
    title = re.sub(r"\s*[(\[](feat|ft|with)\.?\s[^)\]]*[)\]]", "", title, flags=re.I).strip()
    return f"{artist} - {title}"


def best_match(hits: list[Track], artist: str = "", title: str = "") -> Track | None:
    """The hit that is most likely the song itself: a song, not another version of
    it; the official upload ("<Artist> - Topic", VEVO, the artist's channel) first."""
    songs = [t for t in hits if looks_like_song(t, title)
             and not (OTHER_VERSION.search(t.title) and not OTHER_VERSION.search(title))]
    if not songs:
        return None
    name = artist.split(",")[0].split("&")[0].strip()
    return next((t for t in songs if by_artist(t, name) or "vevo" in t.uploader.lower()), songs[0])
