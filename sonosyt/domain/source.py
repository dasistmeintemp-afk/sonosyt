"""What to play: the user's favourites, one of their playlists, or a Sonos favourite."""

from __future__ import annotations


def normalize_source(source) -> dict:
    """A checked music source. Raises ValueError for anything unusable."""
    source = source or {}
    if not isinstance(source, dict):
        raise ValueError("Was soll gespielt werden?")
    kind = source.get("type")
    if kind == "playlist":
        name = str(source.get("name") or "")[:60]
        if not name:
            raise ValueError("Playlist fehlt.")
        return {"type": kind, "name": name}
    if kind == "favorites":
        return {"type": kind}
    if kind == "sonosfav":
        return {"type": kind, "index": int(source.get("index")), "title": str(source.get("title") or "")[:120]}
    raise ValueError("Was soll gespielt werden?")


def describe(source: dict) -> str:
    if source["type"] == "favorites":
        return "Favoriten"
    if source["type"] == "playlist":
        return source["name"]
    return source.get("title") or "Sonos-Favorit"
