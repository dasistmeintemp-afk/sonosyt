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
        self.now = {"state": "PLAYING"}
        self.items = [{"title": "Song", "artist": "Chan", "album_art": "", "video_id": VID,
                       "live": False}]

    def speakers(self, refresh=False):
        return [{"name": "Küche", "ip": "10.0.0.5", "group": ["Küche"]}]

    def get(self, name):
        if name != "Küche":
            raise SonosError(f"nicht gefunden: {name}")
        return FakeDevice()

    def enqueue(self, speaker, items, mode):
        self.enqueued.append((speaker, items, mode))
        return 1

    def control(self, speaker, action, value=None, to=None):
        self.controls.append((speaker, action, value) + ((to,) if to is not None else ()))

    def queue(self, speaker):
        self.get(speaker)
        return list(self.items)

    def set_volume(self, speaker, volume, single=False):
        self.controls.append((speaker, "volume", volume) + (("single",) if single else ()))

    def status(self, speaker):
        self.get(speaker)
        return dict(self.now)

    def rooms(self, speaker):
        return [{"name": "Küche", "volume": 20, "grouped": True, "selected": True}]

    def group(self, speaker, room, join):
        self.controls.append((speaker, "group", room, join))


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
        suggest=lambda q: [q + " live", q + " remix"],
        related=lambda vid: [
            Track("llllllllll1", "Live", live=True), Track("bbbbbbbbbbb", "Other"),
            Track("ddddddddddd", "Fresh"),
        ],
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
        "id": VID, "title": "Song", "uploader": "Chan", "duration": 1, "live": False,
        "thumbnail": f"https://i.ytimg.com/vi/{VID}/hqdefault.jpg",
    }


def test_play_search_term_takes_first_hit_and_prefetches(srv):
    base, app, sonos = srv
    status, _, body = call(base + "/api/play", {"speaker": "Küche", "query": "beatles"})
    assert status == 200, body
    speaker, items, mode = sonos.enqueued[0]
    assert (speaker, mode) == ("Küche", "now")
    assert [(t.id, uri) for t, uri in items] == [(VID, f"http://10.0.0.2:{app.port}/stream/{VID}.mp3")]
    assert app.transcoder.cached(VID)  # converted before Sonos was told to play


def test_play_playlist_url_queues_everything(srv):
    base, _, sonos = srv
    status, _, _ = call(
        base + "/api/play",
        {"speaker": "Küche", "query": "https://www.youtube.com/playlist?list=PL1", "queue": True},
    )
    assert status == 200
    _, items, mode = sonos.enqueued[0]
    assert [t.id for t, _ in items] == [VID, "bbbbbbbbbbb"] and mode == "queue"


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


def test_favorites_and_history_survive_a_restart(srv, tmp_path):
    base, app, _ = srv
    call(base + "/api/search?q=beatles")
    assert call(base + "/api/play", {"speaker": "Küche", "ids": ["bbbbbbbbbbb"], "queue": True})[0] == 200
    status, _, body = call(base + "/api/favorite", {"id": VID})
    assert status == 200 and [t["id"] for t in json.loads(body)["favorites"]] == [VID]
    library = json.loads(call(base + "/api/library")[2])
    assert [t["id"] for t in library["history"]] == ["bbbbbbbbbbb"]
    assert call(base + "/api/favorite", {"id": "nope"})[0] == 400

    from sonosyt.library import Library
    reloaded = Library(app.library.path)
    assert [t.id for t in reloaded.tracks()] == [VID, "bbbbbbbbbbb"]
    assert call(base + "/api/favorite", {"id": VID, "favorite": False})[0] == 200
    assert json.loads(call(base + "/api/library")[2])["favorites"] == []


def test_autoplay_queues_a_fresh_related_track_once(srv):
    base, app, sonos = srv
    sonos.now = {"state": "PLAYING", "video_id": VID, "queue_position": 2, "queue_length": 2,
                 "repeat": 0, "live": False}
    assert call(base + "/api/autoplay", {"speaker": "Küche", "on": True})[0] == 200
    assert json.loads(call(base + "/api/status?speaker=K%C3%BCche")[2])["autoplay"] is True
    app.library.played([Track("bbbbbbbbbbb", "Other")])  # heard recently: skipped

    assert app.autoplay_once("Küche").id == "ddddddddddd"  # not the live one, not the recent one
    speaker, items, mode = sonos.enqueued[-1]
    assert items[0][1].endswith("/stream/ddddddddddd.mp3") and mode == "queue"
    assert app.autoplay_once("Küche") is None  # same track still playing: nothing new
    assert len(sonos.enqueued) == 1

    for change in ({"queue_position": 1}, {"state": "PAUSED_PLAYBACK"}, {"repeat": 1}, {"video_id": None}):
        sonos.now = {**sonos.now, "video_id": "bbbbbbbbbbb", "queue_position": 2, **change}
        assert app.autoplay_once("Küche") is None
    assert call(base + "/api/autoplay", {"speaker": "Bad", "on": True})[0] == 404


def test_rooms_group_and_single_volume(srv):
    base, _, sonos = srv
    assert json.loads(call(base + "/api/rooms?speaker=K%C3%BCche")[2])[0]["name"] == "Küche"
    assert call(base + "/api/group", {"speaker": "Küche", "room": "Bad", "join": True})[0] == 200
    assert call(base + "/api/control", {"speaker": "Küche", "volume": 12, "single": True})[0] == 200
    assert sonos.controls == [("Küche", "group", "Bad", True), ("Küche", "volume", 12, "single")]


def test_live_stream_is_piped_and_ffmpeg_stops_with_the_listener(srv):
    import http.client
    import subprocess
    import time

    base, app, sonos = srv
    # Stands in for "yt-dlp --get-url": an endless generated tone as the stream source.
    app.transcoder.live_url_cmd = lambda vid: ["echo", "anullsrc"]
    real = subprocess.Popen
    started = []

    def popen(cmd, **kwargs):
        if "anullsrc" in cmd and "-i" in cmd:  # swap the URL input for a real-time generated signal
            i = cmd.index("-i")
            cmd = [cmd[0], "-nostdin", "-loglevel", "error", "-re", "-f", "lavfi",
                   "-i", "sine=frequency=440"] + cmd[i + 2:]
        proc = real(cmd, **kwargs)
        started.append(proc)
        return proc

    subprocess.Popen = popen
    try:
        host, port = base[len("http://"):].split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=30)
        conn.request("GET", "/live/llllllllll1.mp3")
        resp = conn.getresponse()
        assert resp.status == 200 and resp.getheader("Content-Type") == "audio/mpeg"
        assert resp.getheader("Content-Length") is None
        assert len(resp.read(20_000)) == 20_000
        resp.close()  # the speaker goes away
        conn.close()
        deadline = time.time() + 15
        while started[-1].poll() is None and time.time() < deadline:
            time.sleep(0.1)
        assert started[-1].poll() is not None, "ffmpeg kept running after the listener left"
    finally:
        subprocess.Popen = real
    assert not list(app.transcoder.cache_dir.glob("llllllllll1*"))  # nothing cached

    app.remember([Track("llllllllll1", "Live", live=True)])
    assert call(base + "/api/play", {"speaker": "Küche", "ids": ["llllllllll1"]})[0] == 200
    assert sonos.enqueued[-1][1][0][1].endswith("/live/llllllllll1.mp3")


def test_suggest_playlists_modes_and_move(srv):
    base, app, sonos = srv
    assert json.loads(call(base + "/api/suggest?q=fred")[2]) == ["fred live", "fred remix"]
    related = json.loads(call(base + f"/api/related?id={VID}")[2])
    assert [t["id"] for t in related] == ["llllllllll1", "bbbbbbbbbbb", "ddddddddddd"]
    assert call(base + "/api/related?id=nope")[0] == 400

    call(base + "/api/search?q=beatles")
    status, _, body = call(base + "/api/playlist", {"name": "  Party   Mix ", "id": VID})
    assert status == 200
    assert [(p["name"], [t["id"] for t in p["tracks"]]) for p in json.loads(body)["playlists"]] == [
        ("Party Mix", [VID])]
    call(base + "/api/playlist", {"name": "Party Mix", "id": VID})  # no duplicates
    call(base + "/api/playlist", {"name": "Party Mix", "id": "bbbbbbbbbbb"})
    from sonosyt.library import Library
    saved = Library(app.library.path).snapshot()["playlists"]  # survives a restart
    assert [t["id"] for t in saved[0]["tracks"]] == [VID, "bbbbbbbbbbb"]
    body = call(base + "/api/playlist", {"name": "Party Mix", "op": "remove", "id": VID})[2]
    assert [t["id"] for t in json.loads(body)["playlists"][0]["tracks"]] == ["bbbbbbbbbbb"]
    assert call(base + "/api/playlist", {"name": "", "id": VID})[0] == 400
    assert call(base + "/api/playlist", {"name": "x", "op": "explode"})[0] == 400
    body = call(base + "/api/playlist", {"name": "Party Mix", "op": "delete"})[2]
    assert json.loads(body)["playlists"] == []

    assert call(base + "/api/play", {"speaker": "Küche", "ids": [VID], "mode": "next"})[0] == 200
    assert sonos.enqueued[-1][2] == "next"
    assert call(base + "/api/play", {"speaker": "Küche", "ids": [VID], "mode": "x"})[0] == 400
    assert call(base + "/api/control", {"speaker": "Küche", "action": "move", "value": 3, "to": 0})[0] == 200
    assert sonos.controls[-1] == ("Küche", "move", 3, 0)


def test_search_and_playing_queue_convert_ahead(srv):
    import time

    base, app, sonos = srv
    app.lookup = lambda q: [Track(VID, "Song", "Chan", 200), Track("llllllllll1", "Live", live=True),
                            Track("bbbbbbbbbbb", "Long", "Chan", 7200)]
    call(base + "/api/search?q=anything")
    deadline = time.time() + 30
    while not app.transcoder.cached(VID) and time.time() < deadline:
        time.sleep(0.1)
    assert app.transcoder.cached(VID)  # likely pick converted before anyone tapped
    assert not app.transcoder.cached("bbbbbbbbbbb")  # two-hour video: not speculatively

    sonos.items = [{"video_id": VID, "live": False}, {"video_id": "bbbbbbbbbbb", "live": False}]
    sonos.now = {"state": "PLAYING", "video_id": VID, "queue_position": 1, "queue_length": 2}
    app._active["Küche"] = time.monotonic()
    app.monitor_once()
    deadline = time.time() + 30
    while not app.transcoder.cached("bbbbbbbbbbb") and time.time() < deadline:
        time.sleep(0.1)
    assert app.transcoder.cached("bbbbbbbbbbb")  # the upcoming track is ready in time
    assert app.prefetch_next("Küche", sonos.now) is None  # asked once per track
