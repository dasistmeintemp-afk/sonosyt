"""What counts as a song: rules for keeping search results, radios and artist
pages to music, and for telling the official upload from the rest."""

from __future__ import annotations

import re

from .track import Track

MIN_SONG = 60
MAX_SONG = 12 * 60   # longer: mixes, compilations, full concerts, podcasts

NOT_MUSIC = re.compile(
    r"\b(podcast|reaction|reacts?|reagiert|interview|tutorial|lesson|unboxing|review|vlog|trailer|"
    r"behind the scenes|making of|compilation|full album|playlist|karaoke|instrumental version|"
    r"\d+\s*(hours?|stunden?)|hour loop|loop)\b", re.I)
CHANNEL_SUFFIX = re.compile(r"\s*-\s*topic$|\s*vevo$|\s*official$|\s*music$", re.I)


# a search for these wants whole performances, however long they are
LONG_WANTED = re.compile(r"\b(live|konzert|concert|set|boiler room|tiny desk|session|festival|tour)\b", re.I)


def looks_like_song(track: Track, query: str = "") -> bool:
    """Song-length, not live, and not obviously something else - unless the search
    asked for exactly that ("karaoke", "full album", "live" ...)."""
    if track.live:
        return False
    too_long = track.duration is not None and track.duration > MAX_SONG and not LONG_WANTED.search(query or "")
    if too_long or (track.duration is not None and track.duration < MIN_SONG):
        return False
    hit = NOT_MUSIC.search(track.title)
    return not hit or bool(NOT_MUSIC.search(query or ""))


def music_only(tracks: list[Track], query: str = "") -> list[Track]:
    """Search results reduced to songs; all of them if that would leave nothing."""
    songs = [t for t in tracks if looks_like_song(t, query)]
    return songs or list(tracks)


def artist_name(track: Track) -> str:
    """Who sings it: "Artist - Topic" channels name it, otherwise "Artist - Song" titles do."""
    uploader = (track.uploader or "").strip()
    if re.search(r"\s-\s*topic$", uploader, re.I):
        return CHANNEL_SUFFIX.sub("", uploader).strip()
    if " - " in track.title:
        left = track.title.split(" - ", 1)[0]
        return re.split(r"\s+(?:feat\.?|ft\.?|x|&|,)\s+", left, maxsplit=1, flags=re.I)[0].strip()
    name = re.sub(r"vevo$", "", uploader, flags=re.I).strip()
    return CHANNEL_SUFFIX.sub("", name).strip() or uploader


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]", "", text.lower())


def by_artist(track: Track, artist: str) -> bool:
    """Uploaded by the artist (their channel, "- Topic", VEVO) or titled "Artist - ..."."""
    want = _key(artist)
    if not want:
        return False
    return _key(artist_name(track)) == want or _key(CHANNEL_SUFFIX.sub("", track.uploader or "")) == want \
        or _key(re.sub(r"vevo$", "", track.uploader or "", flags=re.I)) == want


def artist_songs(tracks: list[Track], artist: str, limit: int = 25) -> list[Track]:
    """An artist page: their own songs first, then songs that at least name them."""
    songs = [t for t in tracks if looks_like_song(t)]
    own = [t for t in songs if by_artist(t, artist)]
    named = [t for t in songs if t not in own and _key(artist) in _key(t.title)]
    seen, out = set(), []
    for t in own + named:
        name = t.title.split(" - ", 1)[1] if " - " in t.title else t.title
        title = _key(re.sub(r"\(.*?\)|\[.*?\]|\b(feat|ft)\.?\s.*$", "", name, flags=re.I))
        if title in seen:   # the same song as video and as audio upload: keep one
            continue
        seen.add(title)
        out.append(t)
    return out[:limit]
