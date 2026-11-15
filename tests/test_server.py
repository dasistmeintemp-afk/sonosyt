import json
import threading
import urllib.error
import urllib.request

import pytest

from sonosyt import server as server_mod
from sonosyt.server import App, make_server
from sonosyt.sonos import SonosError
from sonosyt.transcode import Transcoder
from sonosyt.youtube import Track
from tests.test_transcode import tone_cmd

VID = "abcdefghijk"


class FakeDevice:
    ip_address = "10.0.0.5"


class FakeSonos:
    def __init__(self):
        self.enqueued = []
        self.controls = []

    def speakers(self, refresh=False):
        return [{"name": "Küche", "ip": "10.0.0.5", "group": ["Küche"]}]

    def get(self, name):
        if name != "Küche":
            raise SonosError(f"nicht gefunden: {name}")
        return FakeDevice()

    def enqueue(self, speaker, items, play_now):
        self.enqueued.append((speaker, items, play_now))
        return 1

    def control(self, speaker, action, value=None):
        self.controls.append((speaker, action, value))

    def queue(self, speaker):
        self.get(speaker)
        return [{"title": "Song", "artist": "Chan", "album_art": ""}]

    def set_volume(self, speaker, volume):
        self.controls.append((speaker, "volume", volume))

    def status(self, speaker):
        self.get(speaker)
        return {"state": "PLAYING"}


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "local_ip_for", lambda ip: "10.0.0.2")
    catalog = {
        "beatles": [Track(VID, "Song", "Chan", 1), Track("bbbbbbbbbbb", "Other")],
        "https://www.youtube.com/playlist?list=PL1": [Track(VID, "Song"), Track("bbbbbbbbbbb", "Other")],
        "bbbbbbbbbbb": [Track("bbbbbbbbbbb", "Other")],
    }
    sonos = FakeSonos()
    app = App(
        Transcoder(tmp_path, source_cmd=tone_cmd()), sonos, port=0,
        lookup=lambda q: catalog.get(q, []), prefetch_wait=30,
    )
    httpd = make_server(app, "127.0.0.1")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{app.port}", app, sonos
    httpd.shutdown()
    httpd.server_close()


def call(url, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json"} if data else {}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_ui_and_search(srv):
    base, _, _ = srv
    status, headers, body = call(base + "/")
    assert status == 200 and b"YouTube" in body and "text/html" in headers["Content-Type"]
    status, _, body = call(base + "/api/ping")
    assert status == 200 and json.loads(body)["app"] == "sonosyt"
    status, _, body = call(base + "/api/search?q=beatles")
    assert status == 200
    assert json.loads(body)[0] == {
        "id": VID, "title": "Song", "uploader": "Chan", "duration": 1,
        "thumbnail": f"https://i.ytimg.com/vi/{VID}/hqdefault.jpg",
    }


def test_play_search_term_takes_first_hit_and_prefetches(srv):
    base, app, sonos = srv
    status, _, body = call(base + "/api/play", {"speaker": "Küche", "query": "beatles"})
    assert status == 200, body
    speaker, items, play_now = sonos.enqueued[0]
    assert (speaker, play_now) == ("Küche", True)
    assert [(t.id, uri) for t, uri in items] == [(VID, f"http://10.0.0.2:{app.port}/stream/{VID}.mp3")]
    assert app.transcoder.cached(VID)  # converted before Sonos was told to play


def test_play_playlist_url_queues_everything(srv):
    base, _, sonos = srv
    status, _, _ = call(
        base + "/api/play",
        {"speaker": "Küche", "query": "https://www.youtube.com/playlist?list=PL1", "queue": True},
    )
    assert status == 200
    _, items, play_now = sonos.enqueued[0]
    assert [t.id for t, _ in items] == [VID, "bbbbbbbbbbb"] and play_now is False


def test_play_by_ids_and_errors(srv):
    base, _, sonos = srv
    assert call(base + "/api/play", {"speaker": "Küche", "ids": ["bbbbbbbbbbb"], "queue": True})[0] == 200
    assert sonos.enqueued[0][1][0][0].title == "Other"
    assert call(base + "/api/play", {"speaker": "Bad", "ids": ["bbbbbbbbbbb"]})[0] == 404
    assert call(base + "/api/play", {"speaker": "Küche", "ids": ["../../etc"]})[0] == 400
    assert call(base + "/api/play", {"speaker": "Küche", "query": "nothing"})[0] == 502
    assert call(base + "/api/play", {"ids": ["bbbbbbbbbbb"]})[0] == 400
    # Form posts (what a foreign website could send without CORS) are refused.
    status, _, _ = call(
        base + "/api/control", headers={"Content-Type": "text/plain"}, method="POST"
    )
    assert status == 415


def test_control(srv):
    base, _, sonos = srv
    assert call(base + "/api/control", {"speaker": "Küche", "action": "pause", "volume": 30})[0] == 200
    assert call(base + "/api/control", {"speaker": "Küche", "action": "seek", "value": 42})[0] == 200
    assert sonos.controls == [
        ("Küche", "volume", 30), ("Küche", "pause", None), ("Küche", "seek", 42),
    ]
    assert json.loads(call(base + "/api/queue?speaker=K%C3%BCche")[2])[0]["title"] == "Song"
    assert call(base + "/api/status?speaker=K%C3%BCche")[0] == 200
    assert call(base + "/api/status?speaker=x")[0] == 404


def test_stream_full_range_and_head(srv):
    base, app, _ = srv
    url = f"{base}/stream/{VID}.mp3"
    status, headers, body = call(url)  # first request triggers the conversion
    assert status == 200 and headers["Content-Type"] == "audio/mpeg" and len(body) > 10_000
    app.transcoder.ensure(VID).done.wait(30)
    full = app.transcoder.cached(VID).read_bytes()

    status, headers, body = call(url)
    assert status == 200 and body == full
    assert headers["Content-Length"] == str(len(full)) and headers["Accept-Ranges"] == "bytes"

    status, headers, body = call(url, headers={"Range": "bytes=100-199"})
    assert status == 206 and body == full[100:200]
    assert headers["Content-Range"] == f"bytes 100-199/{len(full)}"

    status, _, body = call(url, headers={"Range": "bytes=5000-"})
    assert status == 206 and body == full[5000:]
    status, _, body = call(url, headers={"Range": "bytes=-300"})
    assert status == 206 and body == full[-300:]
    assert call(url, headers={"Range": f"bytes={len(full)}-"})[0] == 416

    status, headers, body = call(url, method="HEAD")
    assert status == 200 and body == b"" and headers["Content-Length"] == str(len(full))


def test_stream_rejects_bad_paths(srv):
    base, _, _ = srv
    assert call(base + "/stream/short.mp3")[0] == 404
    assert call(base + "/stream/..%2F..%2Fetc%2Fpasswd")[0] == 404
