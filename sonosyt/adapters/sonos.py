"""Sonos discovery and playback control via SoCo. Implements the SpeakerSystem port."""

from __future__ import annotations

import logging
import re
import threading
import time

from ..domain.errors import SpeakerError
from ..domain.track import Track

log = logging.getLogger(__name__)


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


def _group_via_websocket(coordinator_ip: str, coordinator_uid: str, member_uid: str, join: bool) -> None:
    """Add a player to (or remove it from) a group through Sonos' local control API."""
    import asyncio

    from sonos_websocket import SonosWebsocket

    async def run() -> None:
        ws = SonosWebsocket(coordinator_ip)
        try:
            await ws.connect()
            header, data = await ws.get_groups()
            if not header.get("success"):
                raise SpeakerError(f"Gruppen konnten nicht gelesen werden: {data}")
            groups = data.get("groups") or []
            if join:
                group = next(
                    (g for g in groups if g.get("coordinatorId") == coordinator_uid
                     or coordinator_uid in (g.get("playerIds") or [])), None)
                change = {"playerIdsToAdd": [member_uid]}
            else:
                group = next((g for g in groups if member_uid in (g.get("playerIds") or [])), None)
                change = {"playerIdsToRemove": [member_uid]}
            if not group:
                raise SpeakerError("Die Gruppe wurde nicht gefunden.")
            header, data = await ws.send_command(
                {
                    "namespace": "groups:1",
                    "command": "modifyGroupMembers",
                    "householdId": await ws.get_household_id(),
                    "groupId": group["id"],
                },
                change,
            )
            if not header.get("success"):
                raise SpeakerError(f"Sonos hat die Änderung abgelehnt: {data}")
        finally:
            await ws.close()

    asyncio.run(run())


class SonosController:
    def __init__(self, ips: list[str] | None = None, discovery_ttl: float = 60.0):
        self.ips = ips or []
        self.discovery_ttl = discovery_ttl
        self._speakers: list = []
        self._upnp_refused: set[str] = set()
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

    def overview(self) -> list[dict]:
        """One entry per group (named after its coordinator), with what it plays."""
        groups, seen = [], set()
        for dev in self.devices():
            coord = self._coordinator(dev)
            if coord.ip_address in seen:
                continue
            seen.add(coord.ip_address)
            try:
                members = sorted(m.player_name for m in coord.group.members if m.is_visible)
            except Exception:  # noqa: BLE001
                members = [coord.player_name]
            try:
                status = self.status(coord.player_name)
            except Exception as exc:  # noqa: BLE001 - unreachable room: still listed
                log.warning("overview: %s: %s", coord.player_name, exc)
                status = {"state": "", "title": "", "volume": None}
            groups.append({
                "name": coord.player_name,
                "members": members or [coord.player_name],
                "state": status.get("state", ""),
                "title": status.get("title", ""),
                "artist": status.get("artist", ""),
                "album_art": status.get("album_art", ""),
                "video_id": status.get("video_id"),
                "volume": status.get("volume"),
                "muted": bool(status.get("muted")),
            })
        return sorted(groups, key=lambda g: g["name"].lower())

    def get(self, name_or_ip: str):
        """Find a speaker by room name (case-insensitive) or IP address."""
        wanted = (name_or_ip or "").strip().lower()
        for refresh in (False, True):
            for dev in self.devices(refresh):
                if wanted in (dev.player_name.lower(), dev.ip_address):
                    return dev
        raise SpeakerError(f"Sonos-Lautsprecher nicht gefunden: {name_or_ip!r}")

    def address(self, speaker: str) -> str:
        return self.get(speaker).ip_address

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

    def enqueue(self, speaker: str, items: list[tuple[Track, str]], mode: str = "now") -> int:
        """Add tracks to the speaker's queue.

        ``items`` are (track, stream URL) pairs. ``mode``:

        * ``"now"``  - right after the current track, and playback jumps to the
          first of them; whatever was queued before continues afterwards.
        * ``"next"`` - right after the current track, without interrupting it.
        * ``"queue"`` - at the end.

        Returns the 1-based queue position of the first added track.
        """
        if mode not in ("now", "next", "queue"):
            raise SpeakerError(f"Unbekannter Modus: {mode!r}")
        if not items:
            raise SpeakerError("Keine Titel zum Abspielen.")
        coord = self._coordinator(self.get(speaker))
        insert_at = 0  # 0 = append
        if mode in ("now", "next"):
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
        if mode == "now":
            coord.play_from_queue(first - 1)
        return first

    def control(
        self, speaker: str, action: str, value: int | None = None, to: int | None = None
    ) -> None:
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
        elif action == "move":  # queue item at 0-based index ``value`` to index ``to``
            try:
                coord.move_in_queue(int(value), int(to))
            except ValueError as exc:
                raise SpeakerError("Diese Position gibt es in der Warteschlange nicht.") from exc
        elif action == "crossfade":
            coord.cross_fade = bool(value)
        elif action == "mute":
            self._volume_target(self.get(speaker)).mute = bool(value)
        elif action in ("shuffle", "repeat"):
            # Only possible while the queue is playing; Sonos refuses otherwise.
            try:
                if action == "shuffle":
                    coord.shuffle = bool(value)
                else:
                    coord.repeat = {0: False, 1: True, 2: "ONE"}[int(value or 0)]
            except KeyError:
                raise SpeakerError("Ungültiger Wert für Wiederholen.")
            except Exception as exc:  # noqa: BLE001
                raise SpeakerError(
                    "Zufall/Wiederholen geht nur, während die Warteschlange läuft."
                ) from exc
        elif action == "sleep":  # minutes; 0 switches the timer off
            minutes = max(0, int(value or 0))
            coord.set_sleep_timer(minutes * 60 if minutes else None)
        else:
            raise SpeakerError(f"Unbekannte Aktion: {action!r}")

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
            dev.zone_group_state.clear_cache()  # show a regrouping at once, not 5 s later
        except Exception:  # noqa: BLE001
            pass
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
                "bass": self._safe(lambda r=room: r.bass, 0),
                "treble": self._safe(lambda r=room: r.treble, 0),
                "loudness": bool(self._safe(lambda r=room: r.loudness, False)),
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
            raise SpeakerError("Der gewählte Raum gehört immer dazu.")
        coordinator = self._coordinator(dev)
        upnp_error: Exception | None = None
        if other.uid not in self._upnp_refused:
            try:
                if join:
                    other.join(coordinator)
                else:
                    other.unjoin()
                return
            except Exception as exc:  # noqa: BLE001 - the speaker refused
                log.warning("grouping %s with %s over UPnP failed: %s", room, speaker, exc)
                upnp_error = exc
        # Some speakers (e.g. on an older software version than the rest) refuse
        # the classic command. The Sonos app groups through the speakers' newer
        # local control interface instead, so try that as well.
        try:
            _group_via_websocket(coordinator.ip_address, coordinator.uid, other.uid, join)
            self._upnp_refused.add(other.uid)  # next time, skip the failing attempt
        except Exception as exc:  # noqa: BLE001
            log.warning("grouping %s with %s over websocket failed: %s", room, speaker, exc)
            raise SpeakerError(
                f"{room} lässt sich gerade nicht {'hinzufügen' if join else 'herausnehmen'}"
                f" – der Lautsprecher hat abgelehnt ({upnp_error or exc})."
            ) from exc
        for device in (dev, other, coordinator):  # forget the cached grouping
            try:
                device.zone_group_state.clear_cache()
            except Exception:  # noqa: BLE001
                pass

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
            "muted": bool(self._safe(lambda: self._volume_target(dev).mute, False)),
            "crossfade": bool(self._safe(lambda: coord.cross_fade, False)),
        }

    def reachable(self, speaker: str) -> bool:
        """Does the speaker answer on its control port right now?"""
        import urllib.request

        dev = self.get(speaker)
        url = f"http://{dev.ip_address}:1400/xml/device_description.xml"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001
            return False

    def set_eq(self, room: str, bass=None, treble=None, loudness=None) -> dict:
        """Tone controls of one room. Bass and treble range from -10 to 10."""
        dev = self.get(room)
        if bass is not None:
            dev.bass = max(-10, min(10, int(bass)))
        if treble is not None:
            dev.treble = max(-10, min(10, int(treble)))
        if loudness is not None:
            dev.loudness = bool(loudness)
        return {"bass": dev.bass, "treble": dev.treble, "loudness": bool(dev.loudness)}

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
            try:
                uri = item.resources[0].uri or ""
            except (AttributeError, IndexError):
                uri = ""
            ours = re.search(r"/(stream|live)/([A-Za-z0-9_-]{11})\.mp3$", uri)
            out.append({
                "title": getattr(item, "title", "") or "",
                "artist": getattr(item, "creator", "") or "",
                "album_art": art,
                "video_id": ours.group(2) if ours else None,
                "live": bool(ours and ours.group(1) == "live"),
            })
        return out
