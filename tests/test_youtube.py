import pytest

from sonosyt import youtube
from sonosyt.youtube import Track, YouTubeError, lookup


@pytest.fixture
def extract(monkeypatch):
    calls = []
    result = {}

    def fake(target, playlist=False):
        calls.append(target)
        return result

    monkeypatch.setattr(youtube, "_extract", fake)
    return calls, result


def test_search_term_becomes_ytsearch(extract):
    calls, result = extract
    result["entries"] = [
        {"id": "aaaaaaaaaaa", "title": "One", "channel": "Chan", "duration": 61.0},
        {"id": "UCnotAVideoId_channel", "title": "A channel"},
        None,
    ]
    tracks = lookup("some song", limit=5)
    assert calls == ["ytsearch5:some song"]
    assert tracks == [Track("aaaaaaaaaaa", "One", "Chan", 61)]
    assert tracks[0].thumbnail == "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg"


def test_single_video_url_and_bare_id(extract):
    calls, result = extract
    result.update({"id": "bbbbbbbbbbb", "title": "Solo", "uploader": "Up"})
    assert lookup("https://youtu.be/bbbbbbbbbbb") == [Track("bbbbbbbbbbb", "Solo", "Up", None)]
    lookup("bbbbbbbbbbb")
    assert calls == [
        "https://youtu.be/bbbbbbbbbbb",
        "https://www.youtube.com/watch?v=bbbbbbbbbbb",
    ]


def test_rejects_non_youtube_urls(extract):
    calls, _ = extract
    for url in ("https://example.com/video", "http://youtube.com.evil.test/watch?v=x"):
        with pytest.raises(YouTubeError):
            lookup(url)
    assert calls == []


def test_empty_query(extract):
    assert lookup("   ") == []


def test_live_videos_are_flagged(extract):
    _, result = extract
    result["entries"] = [
        {"id": "aaaaaaaaaaa", "title": "Radio", "live_status": "is_live", "duration": 0},
        {"id": "bbbbbbbbbbb", "title": "Was live", "live_status": "was_live", "duration": 30},
    ]
    live, done = lookup("lofi")
    assert live.live and live.duration is None
    assert not done.live and done.duration == 30
    assert Track.from_dict(live.to_dict()) == live


def test_related_uses_the_mix_and_drops_the_video_itself(extract):
    calls, result = extract
    result["entries"] = [{"id": "aaaaaaaaaaa", "title": "Self"}, {"id": "bbbbbbbbbbb", "title": "Next"}]
    assert [t.id for t in youtube.related("aaaaaaaaaaa")] == ["bbbbbbbbbbb"]
    assert calls == ["https://www.youtube.com/watch?v=aaaaaaaaaaa&list=RDaaaaaaaaaaa"]


def test_extract_reads_json_from_the_ytdlp_subprocess(monkeypatch):
    import subprocess

    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, '{"id": "aaaaaaaaaaa", "title": "T"}', "")

    monkeypatch.setattr(youtube.subprocess, "run", run)
    assert lookup("aaaaaaaaaaa") == [Track("aaaaaaaaaaa", "T")]
    assert seen["cmd"][-2:] == ["--", "https://www.youtube.com/watch?v=aaaaaaaaaaa"]
    assert "--no-playlist" in seen["cmd"] and "yt_dlp" in seen["cmd"]

    def fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "WARNING: x\nERROR: Video unavailable\n")

    monkeypatch.setattr(youtube.subprocess, "run", fail)
    with pytest.raises(YouTubeError, match="Video unavailable"):
        lookup("aaaaaaaaaaa")
