"""Alarms: music at a set time on chosen weekdays, in the alarm's own time zone."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

DAY_COUNT = 7  # 0 = Monday ... 6 = Sunday


def normalize_alarm(data: dict) -> dict:
    """A checked, complete alarm. Raises ValueError for anything unusable."""
    if not isinstance(data, dict):
        raise ValueError("Wecker fehlt.")
    time_text = str(data.get("time") or "")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_text):
        raise ValueError("Uhrzeit im Format HH:MM angeben.")
    days = sorted({int(d) for d in data.get("days") or []})
    if any(d < 0 or d >= DAY_COUNT for d in days):
        raise ValueError("Wochentage 0 (Montag) bis 6 (Sonntag).")
    speaker = str(data.get("speaker") or "").strip()
    if not speaker:
        raise ValueError("Raum fehlt.")
    source = data.get("source") or {}
    kind = source.get("type")
    if kind == "playlist":
        clean_source = {"type": kind, "name": str(source.get("name") or "")[:60]}
        if not clean_source["name"]:
            raise ValueError("Playlist fehlt.")
    elif kind == "favorites":
        clean_source = {"type": kind}
    elif kind == "sonosfav":
        clean_source = {"type": kind, "index": int(source.get("index")),
                        "title": str(source.get("title") or "")[:120]}
    else:
        raise ValueError("Was soll der Wecker spielen?")
    zone = str(data.get("tz") or "UTC")
    try:
        ZoneInfo(zone)
    except Exception as exc:  # noqa: BLE001 - any unknown or malformed zone name
        raise ValueError(f"Unbekannte Zeitzone: {zone}") from exc
    alarm_id = str(data.get("id") or uuid.uuid4().hex[:12])
    if not re.fullmatch(r"[A-Za-z0-9]{1,32}", alarm_id):
        raise ValueError("Ungültige Wecker-ID.")
    return {
        "id": alarm_id,
        "time": time_text,
        "days": days,  # empty: once, at the next occurrence
        "speaker": speaker[:100],
        "source": clean_source,
        "volume": max(1, min(100, int(data.get("volume", 20)))),
        "fade": bool(data.get("fade", True)),
        "shuffle": bool(data.get("shuffle", False)),
        "enabled": bool(data.get("enabled", True)),
        "tz": zone,
    }


def occurrence(alarm: dict, now: datetime) -> str | None:
    """A key for this alarm's ringing at ``now`` ("date time"), or None if it is not due.

    ``now`` must carry a time zone. The key lets callers ring each occurrence once.
    """
    if not alarm["enabled"]:
        return None
    local = now.astimezone(ZoneInfo(alarm["tz"]))
    if local.strftime("%H:%M") != alarm["time"]:
        return None
    if alarm["days"] and local.weekday() not in alarm["days"]:
        return None
    return f"{local:%Y-%m-%d} {alarm['time']}"


def start_volume(alarm: dict) -> int:
    """A soft start begins nearly silent; otherwise straight at the chosen level."""
    return min(3, alarm["volume"]) if alarm["fade"] else alarm["volume"]


def ramp(start: int, target: int, steps: int = 15) -> list[int]:
    """Volume levels from just above ``start`` up to ``target``."""
    if target <= start:
        return []
    return [start + (target - start) * i // steps for i in range(1, steps + 1)]
