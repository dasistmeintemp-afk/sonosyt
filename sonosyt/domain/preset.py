"""Presets: music, rooms and volume saved together, started with one tap."""

from __future__ import annotations

import re
import uuid

from .source import normalize_source


def normalize_preset(data: dict) -> dict:
    """A checked, complete preset. Raises ValueError for anything unusable."""
    if not isinstance(data, dict):
        raise ValueError("Voreinstellung fehlt.")
    name = " ".join(str(data.get("name") or "").split())
    if not 1 <= len(name) <= 40:
        raise ValueError("Der Name muss 1 bis 40 Zeichen lang sein.")
    rooms = []
    for room in data.get("rooms") or []:
        room = str(room).strip()[:100]
        if room and room not in rooms:
            rooms.append(room)
    if not rooms:
        raise ValueError("Mindestens einen Raum wählen.")
    preset_id = str(data.get("id") or uuid.uuid4().hex[:12])
    if not re.fullmatch(r"[A-Za-z0-9]{1,32}", preset_id):
        raise ValueError("Ungültige ID.")
    volume = data.get("volume")
    return {
        "id": preset_id,
        "name": name,
        "source": normalize_source(data.get("source")),
        "rooms": rooms[:12],  # the first room plays, the others join it
        "volume": None if volume in (None, "") else max(0, min(100, int(volume))),  # None: leave as is
        "shuffle": bool(data.get("shuffle", False)),
    }
