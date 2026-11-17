import io
import json

import pytest

from sonosyt.adapters.apple_charts import AppleCharts
from sonosyt.application.charts import FRESH, ChartService
from sonosyt.domain.charts import best_match, chart_query, chart_title
from sonosyt.domain.errors import MediaError
from sonosyt.domain.track import Track


def test_chart_query_and_title():
    assert chart_query("Ed Sheeran", "Azizam (feat. Someone)") == "Ed Sheeran - Azizam"
    assert chart_title("de") == "Top-Songs Deutschland" and chart_title("pl") == "Top-Songs PL"


def test_best_match_prefers_the_official_song():
    hits = [Track("aaaaaaaaaaa", "Song (1 Hour Loop)", "Fan", 3600),
            Track("bbbbbbbbbbb", "Song live", "Someone", 200, live=True),
            Track("ccccccccccc", "Song (Lyrics)", "Lyric Channel", 200),
            Track("ddddddddddd", "Song", "Artist - Topic", 201)]
    assert best_match(hits, "Artist", "Song").id == "ddddddddddd"
    assert best_match(hits[:3], "Artist", "Song").id == "ccccccccccc"
    assert best_match(hits[:2], "Artist", "Song") is None
    assert best_match([Track("eeeeeeeeeee", "Mix It Up", "ArtistVEVO", 180)], "Artist", "Mix It Up").id == "eeeeeeeeeee"


def feed(results):
    body = json.dumps({"feed": {"results": results}}).encode()
    return lambda url: (feed.urls.append(url), io.BytesIO(body))[1]


feed.urls = []


def test_apple_feed_is_read():
    source = AppleCharts(opener=feed([
        {"name": "Song", "artistName": "Artist", "artworkUrl100": "https://img/100x100bb.jpg"},
        {"name": "", "artistName": "Nobody"},
    ]))
    assert source.top_songs("de", 5) == [{"title": "Song", "artist": "Artist", "art": "https://img/300x300bb.jpg"}]
    assert feed.urls[-1].endswith("/de/music/most-played/5/songs.json")
    with pytest.raises(ValueError):
        source.top_songs("../x", 5)
    with pytest.raises(MediaError):
        AppleCharts(opener=feed([])).top_songs("de", 5)

    def offline(url):
        raise OSError("no network")

    with pytest.raises(MediaError):
        AppleCharts(opener=offline).top_songs("de", 5)


class Tracks:
    def __init__(self):
        self.searched = []

    def search(self, query, prefetch=False, limit=10):
        self.searched.append(query)
        if "Broken" in query:
            raise MediaError("yt-dlp failed")
        return [Track("aaaaaaaaaaa" if "One" in query else "bbbbbbbbbbb", query, "X - Topic", 200)]


class Source:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def top_songs(self, country, limit):
        self.calls += 1
        if self.fail:
            raise MediaError("offline")
        return [{"title": "One", "artist": "A", "art": ""}, {"title": "Broken", "artist": "B", "art": ""},
                {"title": "Two", "artist": "C", "art": ""}]


def test_service_finds_songs_and_caches():
    now = [1000.0]
    source, tracks = Source(), Tracks()
    service = ChartService(source, tracks, "DE", clock=lambda: now[0], background=False)
    data = service.get()
    assert data["title"] == "Top-Songs Deutschland" and data["error"] is None
    assert [i["rank"] for i in data["items"]] == [1, 2, 3]
    assert data["items"][0]["track"]["id"] == "aaaaaaaaaaa" and data["items"][1]["track"] is None
    assert data["items"][1]["query"] == "B - Broken"
    service.get()
    assert source.calls == 1
    assert tracks.searched.count("A - One") == 1 and tracks.searched.count("B - Broken") == 2  # failures retried

    # an outage keeps the last list
    now[0] += FRESH + 1
    source.fail = True
    assert len(service.get()["items"]) == 3 and source.calls == 2


def test_service_reports_an_outage_without_list():
    source = Source()
    source.fail = True
    data = ChartService(source, Tracks(), clock=lambda: 5e4, background=False).get()
    assert data["items"] == [] and data["error"] == "offline"
