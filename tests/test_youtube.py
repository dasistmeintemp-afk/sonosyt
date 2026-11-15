import pytest

from sonosyt import youtube
from sonosyt.youtube import Track, YouTubeError, lookup


@pytest.fixture
def extract(monkeypatch):
    calls = []
    result = {}

    def fake(target):
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
