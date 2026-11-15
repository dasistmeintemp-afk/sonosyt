"""Sonos discovery and playback control via SoCo."""

from __future__ import annotations

import re
import socket
import threading
import time

from .youtube import Track


class SonosError(Exception):
    pass


def local_ip_for(peer_ip: str) -> str:
    """The local address this machine uses to talk to ``peer_ip``."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((peer_ip, 1400))  # UDP: nothing is sent
        return s.getsockname()[0]


def _hms(seconds: int | None) -> str | None:
    if not seconds:
        return None
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _seconds(hms: str) -> int:
    """"H:MM:SS" as Sonos reports it -> seconds; 0 if unknown."""
    try:
        total = 0
        for part in hms.split(":"):
            total = total * 60 + int(part)
        return total
    except (AttributeError, ValueError):
        return 0


def build_didl(track: Track, uri: str):
    """Queue item carrying title / channel / cover so the Sonos app shows them."""
    from soco.data_structures import DidlMusicTrack, DidlResource

    resource = DidlResource(
        uri=uri,
        protocol_info="http-get:*:audio/mpeg:*",
        duration=None if track.live else _hms(track.duration),
    )
    return DidlMusicTrack(
        title=track.title,
        parent_id="-1",
        item_id="-1",
        resources=[resource],
        creator=track.uploader or "YouTube",
        album="YouTube",
        album_art_uri=track.thumbnail,
    )


class SonosController:
    def __init__(self, ips: list[str] | None = None, discovery_ttl: float = 60.0):
        self.ips = ips or []
        self.discovery_ttl = discovery_ttl
        self._speakers: list = []
        self._discovered_at = 0.0
        self._lock = threading.Lock()

    # -- discovery ---------------------------------------------------------

    def _discover(self) -> list:
        import soco

        if not self.ips:
            return list(soco.discover(timeout=3) or [])
        # Fixed addresses (e.g. speakers in another VLAN, where the multicast
        # search cannot reach them): ask each one for the whole system, so a
        # single address is enough to find every room.
        found = {}
        for ip in self.ips:
            dev = soco.SoCo(ip)
            try:
                zones = list(dev.visible_zones) or [dev]
            except Exception:  # noqa: BLE001 - unreachable; keep it listed as given
                zones = [dev]
            for zone in zones:
                found.setdefault(zone.ip_address, zone)
        return list(found.values())

    def devices(self, refresh: bool = False) -> list:
        with self._lock:
            stale = time.monotonic() - self._discovered_at > self.discovery_ttl
            if refresh or stale or not self._speakers:
                found = []
                for dev in self._discover():
                    try:
                        if dev.is_visible:
                            found.append(dev)
                    except Exception:  # noqa: BLE001 - unreachable speaker
                        continue
                found.sort(key=lambda d: d.player_name.lower())
                self._speakers = found
                self._discovered_at = time.monotonic()
            return list(self._speakers)

    def speakers(self, refresh: bool = False) -> list[dict]:
        out = []
        for dev in self.devices(refresh):
            try:
                members = sorted(m.player_name for m in dev.group.members if m.is_visible)
            except Exception:  # noqa: BLE001
                members = [dev.player_name]
            out.append({"name": dev.player_name, "ip": dev.ip_address, "group": members})
        return out

    def get(self, name_or_ip: str):
        """Find a speaker by room name (case-insensitive) or IP address."""
        wanted = (name_or_ip or "").strip().lower()
        for refresh in (False, True):
            for dev in self.devices(refresh):
                if wanted in (dev.player_name.lower(), dev.ip_address):
                    return dev
        raise SonosError(f"Sonos-Lautsprecher nicht gefunden: {name_or_ip!r}")

    @staticmethod
    def _coordinator(dev):
        group = dev.group
        return group.coordinator if group and group.coordinator else dev

    # -- playback ----------------------------------------------------------

    @staticmethod
    def _volume_target(dev):
        """A grouped speaker's volume means the group's, like in the Sonos app."""
        try:
            group = dev.group
            if group and len([m for m in group.members if m.is_visible]) > 1:
                return group
        except Exception:  # noqa: BLE001
            pass
        return dev

    def enqueue(self, speaker: str, items: list[tuple[Track, str]], play_now: bool) -> int:
        """Add tracks to the speaker's queue.

        ``items`` are (track, stream URL) pairs. With ``play_now`` they go
        right after the current track and playback jumps to the first of them,
        so whatever was queued before continues afterwards; otherwise they are
        appended. Returns the 1-based queue position of the first added track.
        """
        if not items:
            raise SonosError("Keine Titel zum Abspielen.")
        coord = self._coordinator(self.get(speaker))
        insert_at = 0  # 0 = append
        if play_now:
            try:
                current = int(coord.get_current_track_info().get("playlist_position") or 0)
                if 1 <= current <= coord.queue_size:
                    insert_at = current + 1
            except Exception:  # noqa: BLE001 - not playing from the queue
                insert_at = 0
        first = None
        for track, uri in items:
            item = build_didl(track, uri)
            if insert_at:
                try:
                    pos = coord.add_to_queue(item, position=insert_at)
                    insert_at += 1
                except Exception:  # noqa: BLE001 - speaker refused the position
                    insert_at = 0
                    pos = coord.add_to_queue(item)
            else:
                pos = coord.add_to_queue(item)
            if first is None:
                first = pos
        if play_now:
            coord.play_from_queue(first - 1)
        return first

    def control(self, speaker: str, action: str, value: int | None = None) -> None:
        coord = self._coordinator(self.get(speaker))
        methods = {
            "play": "play",
            "pause": "pause",
            "stop": "stop",
            "next": "next",
            "previous": "previous",
            "clear": "clear_queue",
        }
        if action in methods:
            getattr(coord, methods[action])()
        elif action == "seek":
            coord.seek(_hms(max(0, int(value))) or "0:00:00")
        elif action == "play_index":  # 0-based queue index
            coord.play_from_queue(int(value))
        elif action == "remove":
            coord.remove_from_queue(int(value))
        elif action in ("shuffle", "repeat"):
            # Only possible while the queue is playing; Sonos refuses otherwise.
            try:
                if action == "shuffle":
                    coord.shuffle = bool(value)
                else:
                    coord.repeat = {0: False, 1: True, 2: "ONE"}[int(value or 0)]
            except KeyError:
                raise SonosError("Ungültiger Wert für Wiederholen.")
            except Exception as exc:  # noqa: BLE001
                raise SonosError(
                    "Zufall/Wiederholen geht nur, während die Warteschlange läuft."
                ) from exc
        elif action == "sleep":  # minutes; 0 switches the timer off
            minutes = max(0, int(value or 0))
            coord.set_sleep_timer(minutes * 60 if minutes else None)
        else:
            raise SonosError(f"Unbekannte Aktion: {action!r}")

    def set_volume(self, speaker: str, volume: int, single: bool = False) -> None:
        """Set the volume of a room's whole group, or with ``single`` just the room."""
        dev = self.get(speaker)
        target = dev if single else self._volume_target(dev)
        target.volume = max(0, min(100, int(volume)))

    # -- rooms / grouping ----------------------------------------------------

    def rooms(self, speaker: str) -> list[dict]:
        """All rooms, with their own volume and whether they play with ``speaker``."""
        dev = self.get(speaker)
        try:
            members = {m.ip_address for m in dev.group.members}
        except Exception:  # noqa: BLE001
            members = {dev.ip_address}
        out = []
        for room in self.devices():
            try:
                volume = room.volume
            except Exception:  # noqa: BLE001 - unreachable room
                continue
            out.append({
                "name": room.player_name,
                "volume": volume,
                "grouped": room.ip_address in members,
                "selected": room.ip_address == dev.ip_address,
            })
        return out

    def group(self, speaker: str, room: str, join: bool) -> None:
        """Add ``room`` to the group ``speaker`` plays in, or take it out."""
        dev = self.get(speaker)
        other = self.get(room)
        if other.ip_address == dev.ip_address:
            raise SonosError("Der gewählte Raum gehört immer dazu.")
        if join:
            other.join(self._coordinator(dev))
        else:
            other.unjoin()

    def status(self, speaker: str) -> dict:
        dev = self.get(speaker)
        coord = self._coordinator(dev)
        info = coord.get_current_track_info()
        transport = coord.get_current_transport_info()
        uri = info.get("uri") or ""
        ours = re.search(r"/(stream|live)/([A-Za-z0-9_-]{11})\.mp3$", uri)
        mode = self._safe(lambda: coord.play_mode, "NORMAL") or "NORMAL"
        return {
            "speaker": dev.player_name,
            "state": transport.get("current_transport_state", ""),
            "title": info.get("title", ""),
            "artist": info.get("artist", ""),
            "position": _seconds(info.get("position", "")),
            "duration": _seconds(info.get("duration", "")),
            "album_art": info.get("album_art", ""),
            "queue_position": int(info.get("playlist_position") or 0)
            if str(info.get("playlist_position") or "").isdigit() else 0,
            "queue_length": self._safe(lambda: coord.queue_size, 0),
            "video_id": ours.group(2) if ours else None,
            "live": bool(ours and ours.group(1) == "live"),
            "shuffle": "SHUFFLE" in mode,
            "repeat": 2 if "REPEAT_ONE" in mode else int(mode in ("REPEAT_ALL", "SHUFFLE")),
            "sleep": self._safe(coord.get_sleep_timer, 0) or 0,  # seconds left
            "volume": self._volume_target(dev).volume,
        }

    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - optional detail the speaker may refuse
            return default

    def queue(self, speaker: str) -> list[dict]:
        coord = self._coordinator(self.get(speaker))
        out = []
        for item in coord.get_queue(max_items=300):
            art = getattr(item, "album_art_uri", "") or ""
            if art.startswith("/"):  # served by the speaker itself
                art = f"http://{coord.ip_address}:1400{art}"
            out.append({
                "title": getattr(item, "title", "") or "",
                "artist": getattr(item, "creator", "") or "",
                "album_art": art,
            })
        return out
