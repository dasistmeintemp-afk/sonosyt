"""Sonos discovery and playback control via SoCo."""

from __future__ import annotations

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


def build_didl(track: Track, uri: str):
    """Queue item carrying title / channel / cover so the Sonos app shows them."""
    from soco.data_structures import DidlMusicTrack, DidlResource

    resource = DidlResource(
        uri=uri, protocol_info="http-get:*:audio/mpeg:*", duration=_hms(track.duration)
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

        if self.ips:
            return [soco.SoCo(ip) for ip in self.ips]
        return list(soco.discover(timeout=3) or [])

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

    def enqueue(self, speaker: str, items: list[tuple[Track, str]], play_now: bool) -> int:
        """Append tracks to the speaker's queue; optionally start the first.

        ``items`` are (track, stream URL) pairs. Returns the 1-based queue
        position of the first added track.
        """
        coord = self._coordinator(self.get(speaker))
        first = None
        for track, uri in items:
            pos = coord.add_to_queue(build_didl(track, uri))
            if first is None:
                first = pos
        if first is None:
            raise SonosError("Keine Titel zum Abspielen.")
        if play_now:
            coord.play_from_queue(first - 1)
        return first

    def control(self, speaker: str, action: str) -> None:
        coord = self._coordinator(self.get(speaker))
        methods = {
            "play": "play",
            "pause": "pause",
            "stop": "stop",
            "next": "next",
            "previous": "previous",
            "clear": "clear_queue",
        }
        if action not in methods:
            raise SonosError(f"Unbekannte Aktion: {action!r}")
        getattr(coord, methods[action])()

    def set_volume(self, speaker: str, volume: int) -> None:
        self.get(speaker).volume = max(0, min(100, int(volume)))

    def status(self, speaker: str) -> dict:
        dev = self.get(speaker)
        coord = self._coordinator(dev)
        info = coord.get_current_track_info()
        transport = coord.get_current_transport_info()
        return {
            "speaker": dev.player_name,
            "state": transport.get("current_transport_state", ""),
            "title": info.get("title", ""),
            "artist": info.get("artist", ""),
            "position": info.get("position", ""),
            "duration": info.get("duration", ""),
            "album_art": info.get("album_art", ""),
            "queue_position": info.get("playlist_position", ""),
            "volume": dev.volume,
        }
