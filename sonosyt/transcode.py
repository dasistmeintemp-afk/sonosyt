"""Fetch a video's audio with yt-dlp and transcode it to MP3 with ffmpeg.

Sonos cannot play YouTube's (DASH / Opus) streams directly, so every video is
converted to a plain MP3 file in a local cache.  A file that is still being
written can already be streamed ("followed"); once complete it is served as a
regular file, which gives Sonos a duration and lets it seek.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

from .youtube import VIDEO_ID_RE, watch_url

log = logging.getLogger(__name__)


def ytdlp_source_cmd(video_id: str) -> list[str]:
    """Command that writes the best audio stream of a video to stdout."""
    return [
        sys.executable, "-m", "yt_dlp",
        "--quiet", "--no-warnings", "--no-playlist",
        "-f", "bestaudio/best",
        "-o", "-",
        "--", watch_url(video_id),
    ]


def ytdlp_live_url_cmd(video_id: str) -> list[str]:
    """Command that prints the stream (HLS) URL of a running livestream."""
    return [
        sys.executable, "-m", "yt_dlp",
        "--quiet", "--no-warnings", "--no-playlist",
        # Livestreams have no audio-only variant; a small picture is enough.
        "-f", "bestaudio/best[height<=360]/best",
        "--get-url",
        "--", watch_url(video_id),
    ]


class Job:
    """One video being (or having been) converted."""

    def __init__(self, video_id: str, path: Path, final: Path):
        self.video_id = video_id
        self.path = path  # file being written
        self.final = final  # where it ends up on success
        self.done = threading.Event()
        self.error: str | None = None

    @property
    def ok(self) -> bool:
        return self.done.is_set() and self.error is None

    def wait_for_data(self, timeout: float) -> bool:
        """Wait until audio bytes exist or the job ended. True if playable."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.done.is_set():
                return self.error is None
            try:
                if self.path.stat().st_size > 0:
                    return True
            except OSError:
                pass
            time.sleep(0.1)
        return False

    def follow(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        """Yield the audio from the start, tailing the file while it grows."""
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            f = open(self.final, "rb")  # finished in the meantime
        with f:
            while True:
                data = f.read(chunk_size)
                if data:
                    yield data
                elif self.done.is_set():
                    data = f.read(chunk_size)  # drain what raced with done
                    if not data:
                        return
                    yield data
                else:
                    time.sleep(0.2)


class Transcoder:
    def __init__(
        self,
        cache_dir: str | os.PathLike,
        bitrate: str = "192k",
        max_cache_bytes: int = 2 * 1024**3,
        source_cmd: Callable[[str], list[str]] = ytdlp_source_cmd,
        ffmpeg: str = "ffmpeg",
        live_url_cmd: Callable[[str], list[str]] = ytdlp_live_url_cmd,
    ):
        self.live_url_cmd = live_url_cmd
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.bitrate = bitrate
        self.max_cache_bytes = max_cache_bytes
        self.source_cmd = source_cmd
        self.ffmpeg = ffmpeg
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._prefetch: queue.Queue[str] = queue.Queue()
        self._prefetch_workers = 0
        for stale in self.cache_dir.glob("*.part"):  # left over from a crash
            stale.unlink(missing_ok=True)

    def final_path(self, video_id: str) -> Path:
        if not VIDEO_ID_RE.match(video_id):
            raise ValueError(f"invalid YouTube video id: {video_id!r}")
        return self.cache_dir / f"{video_id}.mp3"

    def cached(self, video_id: str) -> Path | None:
        """Path of the finished MP3, if it is in the cache."""
        path = self.final_path(video_id)
        if not path.is_file():
            return None
        try:
            os.utime(path)  # mark as recently used for cache eviction
        except OSError:
            pass
        return path

    def ensure(self, video_id: str) -> Job:
        """Return the running job for a video, starting one if needed."""
        final = self.final_path(video_id)
        with self._lock:
            job = self._jobs.get(video_id)
            if job and not job.done.is_set():
                return job
            if final.is_file():
                job = Job(video_id, final, final)
                job.done.set()
                return job
            part = final.with_suffix(".mp3.part")
            part.touch()
            job = Job(video_id, part, final)
            self._jobs[video_id] = job
        threading.Thread(
            target=self._run, args=(job,), name=f"transcode-{video_id}", daemon=True
        ).start()
        return job

    def prefetch(self, video_ids: list[str], workers: int = 2, backlog: int = 12) -> None:
        """Convert videos ahead of time, a few at once, without blocking.

        Used for things likely to be played next (top search hits, queued
        tracks). A video the speaker asks for directly never waits behind these.
        """
        for video_id in video_ids:
            path = self.final_path(video_id)  # validates the id
            with self._lock:
                if path.is_file() or video_id in self._jobs:
                    continue
            if self._prefetch.qsize() >= backlog:
                return
            self._prefetch.put(video_id)
        with self._lock:
            start = max(0, min(workers, self._prefetch.qsize()) - self._prefetch_workers)
            self._prefetch_workers += start
        for _ in range(start):
            threading.Thread(target=self._prefetch_loop, name="prefetch", daemon=True).start()

    def _prefetch_loop(self) -> None:
        while True:
            try:
                video_id = self._prefetch.get(timeout=30)
            except queue.Empty:
                with self._lock:
                    self._prefetch_workers -= 1
                return
            try:
                self.ensure(video_id).done.wait(1800)
            except Exception:  # noqa: BLE001 - best effort
                log.exception("prefetch of %s failed", video_id)

    def live(self, video_id: str, chunk_size: int = 16 * 1024) -> Iterator[bytes]:
        """MP3 audio of a running livestream, until the consumer stops.

        Nothing is cached: ffmpeg reads the stream and is killed as soon as
        the generator is closed (the speaker disconnected).
        """
        watch_url(video_id)  # validates the id
        found = subprocess.run(
            self.live_url_cmd(video_id), capture_output=True, text=True, timeout=90
        )
        urls = [line for line in found.stdout.splitlines() if line.strip()]
        if found.returncode != 0 or not urls:
            lines = (found.stderr or "").strip().splitlines()
            raise RuntimeError(lines[-1] if lines else "Livestream nicht verfügbar")
        ffmpeg = subprocess.Popen(
            [
                self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-i", urls[0],
                "-vn", "-map_metadata", "-1",
                "-c:a", "libmp3lame", "-b:a", self.bitrate, "-ar", "44100", "-ac", "2",
                "-f", "mp3", "pipe:1",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            while True:
                data = ffmpeg.stdout.read(chunk_size)
                if not data:
                    return
                yield data
        finally:
            if ffmpeg.poll() is None:
                ffmpeg.kill()
            ffmpeg.wait()
            ffmpeg.stdout.close()

    def _run(self, job: Job) -> None:
        source = ffmpeg = None
        try:
            source = subprocess.Popen(
                self.source_cmd(job.video_id),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            ffmpeg = subprocess.Popen(
                [
                    self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-i", "pipe:0",
                    "-vn", "-map_metadata", "-1",
                    "-c:a", "libmp3lame", "-b:a", self.bitrate, "-ar", "44100", "-ac", "2",
                    "-f", "mp3", "-y", str(job.path),
                ],
                stdin=source.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            source.stdout.close()  # ffmpeg owns the pipe now
            # Drain the source's stderr concurrently so neither process blocks.
            src_err: list[bytes] = []
            reader = threading.Thread(
                target=lambda: src_err.append(source.stderr.read()), daemon=True
            )
            reader.start()
            _, ff_err = ffmpeg.communicate()
            source.wait()
            reader.join(timeout=5)
            if source.returncode != 0 or ffmpeg.returncode != 0:
                detail = (b"".join(src_err) + b"\n" + (ff_err or b"")).decode(
                    "utf-8", "replace"
                ).strip()
                raise RuntimeError(detail[-2000:] or "transcoding failed")
            if job.path.stat().st_size == 0:
                raise RuntimeError("transcoding produced no audio")
            os.replace(job.path, job.final)
            job.path = job.final
            log.info("cached %s (%d kB)", job.video_id, job.final.stat().st_size // 1024)
        except Exception as exc:  # noqa: BLE001 - reported to the HTTP client
            job.error = str(exc) or exc.__class__.__name__
            log.error("transcoding %s failed: %s", job.video_id, job.error)
            for proc in (source, ffmpeg):
                if proc and proc.poll() is None:
                    proc.kill()
            job.path.unlink(missing_ok=True)
        finally:
            job.done.set()
            with self._lock:
                if self._jobs.get(job.video_id) is job:
                    del self._jobs[job.video_id]
            if job.error is None:
                self._evict(keep=job.final)

    def _evict(self, keep: Path) -> None:
        """Delete least recently used files until the cache fits its limit."""
        files = []
        for path in self.cache_dir.glob("*.mp3"):
            try:
                st = path.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, path))
        total = sum(size for _, size, _ in files)
        for _, size, path in sorted(files):
            if total <= self.max_cache_bytes:
                break
            if path == keep:
                continue
            path.unlink(missing_ok=True)
            total -= size
