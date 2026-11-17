"""What to play: the user's favourites or one of their playlists."""

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
    if kind in ("favorites", "sonosfav"):   # Sonos favourites are no longer offered: favourites instead
        return {"type": "favorites"}
    raise ValueError("Was soll gespielt werden?")


def describe(source: dict) -> str:
    if source["type"] == "favorites":
        return "Favoriten"
    return source["name"]
