"""LRCLIB (lrclib.net), a free and open lyrics database. Implements the LyricsSource port."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

API = "https://lrclib.net/api/search?"


class LrclibSource:
    def search(self, params: dict) -> list[dict]:
        req = urllib.request.Request(
            API + urllib.parse.urlencode(params), headers={"User-Agent": "sonosyt/0.1"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, list) else []
