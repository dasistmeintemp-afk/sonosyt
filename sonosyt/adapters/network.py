"""The machine's network. Implements the Network port."""

from __future__ import annotations

import socket
import urllib.request


class SystemNetwork:
    def local_address_for(self, peer: str) -> str:
        """The local address this machine uses to talk to ``peer`` (the speakers fetch from it)."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((peer, 1400))  # UDP: nothing is sent
            return s.getsockname()[0]

    def youtube_reachable(self) -> bool:
        try:
            with urllib.request.urlopen("https://www.youtube.com/generate_204", timeout=6):
                return True
        except Exception:  # noqa: BLE001
            return False
