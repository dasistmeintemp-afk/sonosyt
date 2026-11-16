import os
import sys
import time

import pytest

from sonosyt.adapters.transcoder import Transcoder

VID = "abcdefghijk"


def tone_cmd(seconds=1):
    """Stands in for yt-dlp: writes a WAV sine tone to stdout."""
    return lambda _vid: [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-f", "wav", "pipe:1",
    ]


def test_transcodes_to_cached_mp3(tmp_path):
    tc = Transcoder(tmp_path, source_cmd=tone_cmd())
    assert tc.cached(VID) is None
    job = tc.ensure(VID)
    assert job.done.wait(30)
    assert job.ok, job.error
    path = tc.cached(VID)
    assert path == tmp_path / f"{VID}.mp3"
    data = path.read_bytes()
    assert len(data) > 10_000
    assert data[:3] == b"ID3" or data[0] == 0xFF  # MP3 with or without ID3 tag
    assert not list(tmp_path.glob("*.part"))
    # A finished video is not converted again.
    assert tc.ensure(VID).ok


def test_follow_streams_a_growing_file_completely(tmp_path):
    # The source trickles data so that follow() starts before the file is complete.
    script = (
        "import subprocess,sys,time\n"
        "p = subprocess.run(['ffmpeg','-nostdin','-loglevel','error','-f','lavfi','-i',"
        "'sine=frequency=440:duration=20','-f','wav','pipe:1'], capture_output=True)\n"
        "d = p.stdout\n"
        "for i in range(0, len(d), 200000):\n"
        "    sys.stdout.buffer.write(d[i:i+200000]); sys.stdout.buffer.flush(); time.sleep(0.15)\n"
    )
    tc = Transcoder(tmp_path, source_cmd=lambda _vid: [sys.executable, "-c", script])
    job = tc.ensure(VID)
    assert tc.ensure(VID) is job  # concurrent requests share one conversion
    assert job.wait_for_data(30)
    assert not job.done.is_set()
    streamed = b"".join(job.follow())
    assert job.ok, job.error
    final = (tmp_path / f"{VID}.mp3").read_bytes()
    assert len(streamed) == len(final)
    # ffmpeg patches the first (Xing) frame once the length is known; the audio matches.
    assert streamed[2000:] == final[2000:]


def test_failed_source_reports_error_and_leaves_no_files(tmp_path):
    tc = Transcoder(
        tmp_path,
        source_cmd=lambda _vid: [sys.executable, "-c", "import sys; sys.exit('video unavailable')"],
    )
    job = tc.ensure(VID)
    assert job.done.wait(30)
    assert not job.ok
    assert "video unavailable" in job.error
    assert job.wait_for_data(1) is False
    assert list(tmp_path.iterdir()) == []


def test_cache_eviction_drops_least_recently_used(tmp_path):
    old = tmp_path / "oldoldoldol.mp3"
    old.write_bytes(b"x" * 50_000)
    past = time.time() - 3600
    os.utime(old, (past, past))
    tc = Transcoder(tmp_path, source_cmd=tone_cmd(), max_cache_bytes=40_000)
    job = tc.ensure(VID)
    assert job.done.wait(30) and job.ok
    time.sleep(0.2)
    assert not old.exists()
    assert (tmp_path / f"{VID}.mp3").exists()  # the newest file always survives


def test_rejects_bad_ids(tmp_path):
    tc = Transcoder(tmp_path, source_cmd=tone_cmd())
    for bad in ("../etc/passwd", "short", "abcdefghijk.mp3"):
        with pytest.raises(ValueError):
            tc.ensure(bad)
