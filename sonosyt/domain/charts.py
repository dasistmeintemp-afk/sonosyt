"""Rules for turning a hit list (artist and title) into YouTube videos."""

from __future__ import annotations

import re

from .track import Track

COUNTRIES = {"de": "Deutschland", "at": "Österreich", "ch": "Schweiz", "us": "USA", "gb": "Großbritannien",
             "fr": "Frankreich", "it": "Italien", "es": "Spanien", "nl": "Niederlande"}
MAX_SONG = 12 * 60  # longer hits are mixes, compilations or full concerts
AVOID = re.compile(r"\b(1 hour|10 hours|stunde|mix|compilation|playlist|karaoke|reaction|cover)\b", re.I)


def chart_title(country: str) -> str:
    return "Top-Songs " + COUNTRIES.get(country.lower(), country.upper())


def chart_query(artist: str, title: str) -> str:
    # "(feat. X)" and "- Remastered" make YouTube's search worse, not better
    title = re.sub(r"\s*[(\[](feat|ft|with)\.?\s[^)\]]*[)\]]", "", title, flags=re.I).strip()
    return f"{artist} - {title}"


def best_match(hits: list[Track], artist: str = "", title: str = "") -> Track | None:
    """The hit that is most likely the song itself: not live, not a long mix; the
    official upload ("<Artist> - Topic", "VEVO" or the artist's channel) first."""
    songs = [t for t in hits if not t.live and (t.duration is None or 60 <= t.duration <= MAX_SONG)
             and not (AVOID.search(t.title) and not AVOID.search(title))]
    if not songs:
        return None
    name = artist.split(",")[0].split("&")[0].strip().lower()

    def official(t: Track) -> bool:
        channel = t.uploader.lower()
        return bool(name) and (channel.startswith(name) or "vevo" in channel)

    return next((t for t in songs if official(t)), songs[0])
