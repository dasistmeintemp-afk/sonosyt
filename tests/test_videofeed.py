import io
import urllib.error

import pytest

from sonosyt.adapters import videofeed
from sonosyt.adapters.videofeed import PIECE, Source, YouTubeVideoFeed
from sonosyt.domain.errors import MediaError

VID = "aaaaaaaaaaa"
DATA = bytes(range(256)) * 40  # 10240 bytes


class Resp(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers


def fake_youtube(log, fail=()):
    """An opener that serves DATA honouring Range, and fails with the codes in ``fail`` first."""
    fail = list(fail)

    def opener(req):
        log.append((req.full_url, req.get_header("Range"), req.get_header("User-agent")))
        if fail:
            raise urllib.error.HTTPError(req.full_url, fail.pop(0), "nope", {}, None)
        start, end = (int(x) for x in req.get_header("Range")[6:].split("-"))
        if start >= len(DATA):
            raise urllib.error.HTTPError(req.full_url, 416, "range", {}, None)
        end = min(end, len(DATA) - 1)
        return Resp(DATA[start:end + 1], {"Content-Range": f"bytes {start}-{end}/{len(DATA)}"})

    return opener


def counting_resolve(calls):
    def resolve(video_id):
        calls.append(video_id)
        return Source(f"https://yt.example/{video_id}?n={len(calls)}", {"User-Agent": "UA"}, "video/mp4")
    return resolve


def test_pieces_are_bounded_and_ranges_passed_on():
    log, calls = [], []
    feed = YouTubeVideoFeed(resolve=counting_resolve(calls), opener=fake_youtube(log))
    piece = feed.open(VID, 100, 199)
    assert (piece.start, piece.end, piece.size, piece.content_type) == (100, 199, len(DATA), "video/mp4")
    assert b"".join(piece.chunks) == DATA[100:200]
    feed.open(VID, 0, None)
    assert log[1][1] == f"bytes=0-{PIECE - 1}"  # open-ended requests are cut into pieces
    assert log[0][2] == "UA"  # yt-dlp's headers go along
    assert calls == [VID]  # looked up once


def test_expired_address_is_looked_up_again():
    log, calls = [], []
    feed = YouTubeVideoFeed(resolve=counting_resolve(calls), opener=fake_youtube(log, fail=[403]))
    feed.warm(VID)
    piece = feed.open(VID, 0, 9)
    assert b"".join(piece.chunks) == DATA[:10]
    assert calls == [VID, VID] and log[0][0].endswith("n=1") and log[1][0].endswith("n=2")


def test_lookups_expire_with_time():
    calls, now = [], [0.0]
    feed = YouTubeVideoFeed(resolve=counting_resolve(calls), opener=fake_youtube([]), clock=lambda: now[0])
    feed.warm(VID)
    feed.warm(VID)
    now[0] = videofeed.LIFETIME + 1
    feed.warm(VID)
    assert calls == [VID, VID]


def test_failures():
    feed = YouTubeVideoFeed(resolve=counting_resolve([]), opener=fake_youtube([], fail=[500]))
    with pytest.raises(MediaError):
        feed.open(VID, 0, 9)
    feed = YouTubeVideoFeed(resolve=counting_resolve([]), opener=fake_youtube([]))
    with pytest.raises(ValueError):
        feed.open(VID, len(DATA) + 5, None)


def test_resolve_reads_yt_dlp_output(monkeypatch):
    class Proc:
        returncode = 0
        stderr = ""
        stdout = '{"url": "https://r1.googlevideo.com/x", "ext": "webm", "http_headers": {"A": "b"}}'

    seen = {}
    monkeypatch.setattr(videofeed.subprocess, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd) and Proc())
    source = videofeed.resolve(VID)
    assert source == Source("https://r1.googlevideo.com/x", {"A": "b"}, "video/webm")
    assert "-f" in seen["cmd"] and seen["cmd"][-1].endswith(VID)

    Proc.stdout = '{"url": "", "ext": "mp4"}'
    with pytest.raises(MediaError):
        videofeed.resolve(VID)
