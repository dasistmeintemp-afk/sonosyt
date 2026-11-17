"""Keeps yt-dlp current: YouTube changes often and old versions stop working."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading

log = logging.getLogger(__name__)

INTERVAL = 24 * 3600


def ytdlp_version() -> str:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, timeout=60,
        )
        return proc.stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def update_ytdlp() -> bool:
    """Upgrade yt-dlp with pip. True if a newer version was installed."""
    before = ytdlp_version()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
             "--disable-pip-version-check", "--no-cache-dir", "yt-dlp[default]"],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("yt-dlp update failed: %s", exc)
        return False
    if proc.returncode != 0:
        lines = (proc.stderr or "").strip().splitlines()
        log.warning("yt-dlp update failed: %s", lines[-1] if lines else "pip error")
        return False
    after = ytdlp_version()
    if after != before:
        log.info("yt-dlp updated: %s -> %s", before, after)
        return True
    log.info("yt-dlp is current (%s)", after)
    return False


def start_auto_update(interval: float = INTERVAL) -> threading.Thread:
    """Update now and then once a day, in the background."""
    stop = threading.Event()

    def loop() -> None:
        while True:
            update_ytdlp()
            if stop.wait(interval):
                return

    thread = threading.Thread(target=loop, name="ytdlp-update", daemon=True)
    thread.stop = stop  # type: ignore[attr-defined]
    thread.start()
    return thread


class YtDlpTools:
    """The Tools port."""

    def ytdlp_version(self) -> str:
        return ytdlp_version()

    def latest_server_version(self) -> str | None:
        return latest_server_version()


# The published server image. Its tags are the release versions (0.2.<build>).
IMAGE = "dasistmeintemp-afk/sonosyt"


def latest_server_version(image: str = IMAGE, opener=None) -> str | None:
    """The newest version of the server image on ghcr.io, or None if unknown.

    Anonymous, as Docker itself does it: a pull token first, then the tag list.
    """
    import json
    import re
    import urllib.request

    opener = opener or (lambda req: urllib.request.urlopen(req, timeout=10))
    try:
        with opener(f"https://ghcr.io/token?scope=repository:{image}:pull") as resp:
            token = json.loads(resp.read())["token"]
        req = urllib.request.Request(f"https://ghcr.io/v2/{image}/tags/list?n=1000",
                                     headers={"Authorization": f"Bearer {token}"})
        with opener(req) as resp:
            tags = json.loads(resp.read()).get("tags") or []
    except (OSError, ValueError, KeyError) as exc:
        log.info("update check failed: %s", exc)
        return None
    versions = [t for t in tags if re.fullmatch(r"\d+\.\d+\.\d+", t)]
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")), default=None)
