import json
import threading
import urllib.error
import urllib.request

import pytest

from sonosyt.adapters.http.server import make_server
from sonosyt.adapters.transcoder import Transcoder
from sonosyt.bootstrap import build
from sonosyt.domain.errors import MediaError, SpeakerError
from sonosyt.domain.track import Track
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
            raise SpeakerError(f"nicht gefunden: {name}")
        return FakeDevice()

    def address(self, name):
        return self.get(name).ip_address

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

    def reachable(self, speaker):
        return True

    def set_eq(self, room, bass=None, treble=None, loudness=None):
        self.controls.append((room, "eq", bass, treble, loudness))
        return {"bass": bass or 0, "treble": treble or 0, "loudness": bool(loudness)}

    def rooms(self, speaker):
        return [{"name": "Küche", "volume": 20, "grouped": True, "selected": True}]

    def group(self, speaker, room, join):
        self.controls.append((speaker, "group", room, join))

    def overview(self):
        return [{"name": "Küche", "members": ["Küche"], "state": self.now.get("state", ""), "title": "Song",
                 "artist": "Chan", "album_art": "", "video_id": VID, "volume": 20, "muted": False},
                {"name": "Bad", "members": ["Bad"], "state": "STOPPED", "title": "", "artist": "",
                 "album_art": "", "video_id": None, "volume": 5, "muted": False}]


class FakeCatalog:
    """Stands in for YouTube; tests replace the functions as needed."""

    def __init__(self, entries):
        self.lookup = lambda q, limit=10: entries.get(q, [])
        self.related = lambda vid: [
            Track("llllllllll1", "Live", live=True), Track("bbbbbbbbbbb", "Other"),
            Track("ddddddddddd", "Fresh"),
        ]
        self.suggest = lambda q: [q + " live", q + " remix"]

        def no_cover(vid):
            raise MediaError("kein Cover")

        self.thumbnail = no_cover


class FakeNetwork:
    def local_address_for(self, peer):
        return "10.0.0.2"

    def youtube_reachable(self):
        return False


class FakeTools:
    latest = None

    def ytdlp_version(self):
        return "2026.01.01"

    def latest_server_version(self):
        return self.latest


class FakeLyrics:
    def search(self, params):
        return []


class Env:
    """What a test needs: the running server, the application and the fakes behind it."""

    def __init__(self, base, app, sonos, catalog, converter):
        self.base, self.app, self.sonos, self.catalog, self.converter = base, app, sonos, catalog, converter
        self.repo = app.library.repository  # the JSON library adapter

    @property
    def port(self):
        return self.app.playback.port

    def __iter__(self):  # base, env, sonos = srv
        return iter((self.base, self, self.sonos))


@pytest.fixture
def srv(tmp_path):
    catalog = FakeCatalog({
        "beatles": [Track(VID, "Song", "Chan", 1), Track("bbbbbbbbbbb", "Other")],
        "https://www.youtube.com/playlist?list=PL1": [Track(VID, "Song"), Track("bbbbbbbbbbb", "Other")],
        "bbbbbbbbbbb": [Track("bbbbbbbbbbb", "Other")],
    })
    sonos = FakeSonos()
    converter = Transcoder(tmp_path, source_cmd=tone_cmd())
    app = build(tmp_path, port=0, prefetch_wait=30, speakers=sonos, catalog=catalog, converter=converter,
                network=FakeNetwork(), tools=FakeTools(), lyrics_source=FakeLyrics())
    httpd = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield Env(f"http://127.0.0.1:{app.playback.port}", app, sonos, catalog, converter)
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
    assert app.converter.cached(VID)  # converted before Sonos was told to play


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
    app.converter.ensure(VID).done.wait(30)
    full = app.converter.cached(VID).read_bytes()

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

    from sonosyt.adapters.json_library import Library
    reloaded = Library(app.repo.path)
    assert [t.id for t in reloaded.tracks()] == [VID, "bbbbbbbbbbb"]
    assert call(base + "/api/favorite", {"id": VID, "favorite": False})[0] == 200
    assert json.loads(call(base + "/api/library")[2])["favorites"] == []


def test_autoplay_queues_a_fresh_related_track_once(srv):
    base, app, sonos = srv
    sonos.now = {"state": "PLAYING", "video_id": VID, "queue_position": 2, "queue_length": 2,
                 "repeat": 0, "live": False}
    assert call(base + "/api/autoplay", {"speaker": "Küche", "on": True})[0] == 200
    assert json.loads(call(base + "/api/status?speaker=K%C3%BCche")[2])["autoplay"] is True
    app.repo.played([Track("bbbbbbbbbbb", "Other")])  # heard recently: skipped

    assert app.app.autoplay.once("Küche").id == "ddddddddddd"  # not the live one, not the recent one
    speaker, items, mode = sonos.enqueued[-1]
    assert items[0][1].endswith("/stream/ddddddddddd.mp3") and mode == "queue"
    assert app.app.autoplay.once("Küche") is None  # same track still playing: nothing new
    assert len(sonos.enqueued) == 1

    for change in ({"queue_position": 1}, {"state": "PAUSED_PLAYBACK"}, {"repeat": 1}, {"video_id": None}):
        sonos.now = {**sonos.now, "video_id": "bbbbbbbbbbb", "queue_position": 2, **change}
        assert app.app.autoplay.once("Küche") is None
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
    app.converter.live_url_cmd = lambda vid: ["echo", "anullsrc"]
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
    assert not list(app.converter.cache_dir.glob("llllllllll1*"))  # nothing cached

    app.app.tracks.remember([Track("llllllllll1", "Live", live=True)])
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
    from sonosyt.adapters.json_library import Library
    saved = Library(app.repo.path).snapshot()["playlists"]  # survives a restart
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
    app.catalog.lookup = lambda q, limit=10: [Track(VID, "Song", "Chan", 200), Track("llllllllll1", "Live", live=True),
                            Track("bbbbbbbbbbb", "Long", "Chan", 7200)]
    call(base + "/api/search?q=anything")
    deadline = time.time() + 30
    while not app.converter.cached(VID) and time.time() < deadline:
        time.sleep(0.1)
    assert app.converter.cached(VID)  # likely pick converted before anyone tapped
    assert not app.converter.cached("bbbbbbbbbbb")  # two-hour video: not speculatively

    sonos.items = [{"video_id": VID, "live": False}, {"video_id": "bbbbbbbbbbb", "live": False}]
    sonos.now = {"state": "PLAYING", "video_id": VID, "queue_position": 1, "queue_length": 2}
    app.app.playback.mark_active("Küche")
    app.app.monitor.once()
    deadline = time.time() + 30
    while not app.converter.cached("bbbbbbbbbbb") and time.time() < deadline:
        time.sleep(0.1)
    assert app.converter.cached("bbbbbbbbbbb")  # the upcoming track is ready in time
    assert app.app.monitor.prefetch_next("Küche", sonos.now) is None  # asked once per track


def test_info_and_clearing_cache_and_history(srv):
    base, app, _ = srv
    call(base + "/api/search?q=beatles")
    assert call(base + "/api/play", {"speaker": "Küche", "ids": [VID]})[0] == 200  # converts VID
    info = json.loads(call(base + "/api/info")[2])
    assert info["version"] and info["ytdlp"]
    assert info["cache"]["files"] == 1 and info["cache"]["bytes"] > 10_000
    assert json.loads(call(base + "/api/library")[2])["history"]

    status, _, body = call(base + "/api/cache/clear", {})
    assert status == 200 and json.loads(body)["removed"] == 1
    assert json.loads(body)["cache"] == {"files": 0, "bytes": 0, "max_bytes": app.converter.max_cache_bytes}
    assert not app.converter.cached(VID)
    assert json.loads(call(base + "/api/history/clear", {})[2])["history"] == []
    call(base + f"/stream/{VID}.mp3")  # a cleared track simply converts again on demand
    app.converter.ensure(VID).done.wait(30)
    assert app.converter.cached(VID)


def test_long_videos_resume_where_they_were_left(srv):
    import time

    base, app, sonos = srv
    call(base + "/api/search?q=beatles")
    long_playing = {"state": "PLAYING", "video_id": VID, "duration": 6000, "position": 2590,
                    "queue_position": 1, "queue_length": 1, "live": False}
    sonos.now = long_playing
    app.app.playback.mark_active("Küche")
    app.app.monitor.once()
    assert app.repo.position(VID) == 2590
    app.app.playback.remember_position({**long_playing, "duration": 300})        # short clip: never stored
    app.app.playback.remember_position({**long_playing, "position": 2600})       # barely moved: no rewrite
    assert app.repo.position(VID) == 2590
    from sonosyt.adapters.json_library import Library
    assert Library(app.repo.path).position(VID) == 2590          # survives a restart

    status, _, body = call(base + "/api/play", {"speaker": "Küche", "ids": [VID]})
    assert json.loads(body)["resumed"] == 2590
    assert sonos.controls[-1] == ("Küche", "seek", 2590)
    # Queued or several at once: plays from the start.
    assert json.loads(call(base + "/api/play", {"speaker": "Küche", "ids": [VID], "mode": "queue"})[2])["resumed"] == 0

    app.app.playback.remember_position({**long_playing, "position": 5970})       # reached the end: forgotten
    assert app.repo.position(VID) == 0
    assert json.loads(call(base + "/api/play", {"speaker": "Küche", "ids": [VID]})[2])["resumed"] == 0


def test_more_results_bulk_playlists_eq_and_diagnose(srv, monkeypatch):
    import urllib.request

    base, app, sonos = srv
    seen = []
    app.catalog.lookup = lambda q, limit=10: seen.append(limit) or [Track(VID, "Song", "Chan", 1)]
    call(base + "/api/search?q=x")
    call(base + "/api/search?q=x&limit=25")
    call(base + "/api/search?q=x&limit=999")
    assert seen == [10, 25, 30]

    app.app.tracks.remember([Track("bbbbbbbbbbb", "Other")])
    body = call(base + "/api/playlist", {"name": "Import", "op": "set",
                                         "ids": [VID, "bbbbbbbbbbb", VID]})[2]
    assert [t["id"] for t in json.loads(body)["playlists"][0]["tracks"]] == [VID, "bbbbbbbbbbb"]
    assert call(base + "/api/playlist", {"name": "Import", "op": "set", "ids": []})[0] == 400

    assert json.loads(call(base + "/api/eq", {"room": "Küche", "bass": 4})[2])["bass"] == 4
    assert sonos.controls[-1] == ("Küche", "eq", 4, None, None)

    real = urllib.request.urlopen
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None, **kw: (
        (_ for _ in ()).throw(OSError("blocked")) if "youtube" in str(url) else real(url, timeout=timeout)))
    diagnosis = json.loads(call(base + "/api/diagnose?speaker=K%C3%BCche")[2])
    assert diagnosis == {"youtube": False, "speaker": True, "fetched_ago": None,
                         "stream_base": f"http://10.0.0.2:{app.port}"}
    call(base + f"/stream/{VID}.mp3")
    assert json.loads(call(base + "/api/diagnose?speaker=K%C3%BCche")[2])["fetched_ago"] <= 5


def test_foreign_host_names_are_refused(srv):
    from sonosyt.adapters.http.server import host_allowed

    base, app, _ = srv
    for ok in (None, "", "192.168.1.49:8327", "192.168.1.49", "localhost:8327", "tower", "tower.local:8327",
               "nas.fritz.box", "[fe80::1]:8327", "TOWER.LOCAL"):
        assert host_allowed(ok), ok
    for bad in ("evil.example.com", "evil.example.com:8327", "192.168.1.49.evil.net"):
        assert not host_allowed(bad), bad
    assert host_allowed("music.example.org:443", frozenset({"music.example.org"}))

    status, _, body = call(base + "/api/speakers", headers={"Host": "evil.example.com"})
    assert status == 403 and "--allow-host" in json.loads(body)["error"]
    assert call(base + "/api/control", {"speaker": "Küche", "action": "pause"},
                headers={"Host": "evil.example.com"})[0] == 403
    assert call(base + "/api/speakers", headers={"Host": "tower.local:8327"})[0] == 200


def test_unplayable_video_is_reported_instead_of_silently_queued(srv):
    import sys
    from sonosyt.domain.playback import playback_error

    base, app, sonos = srv
    app.converter.source_cmd = lambda vid: [sys.executable, "-c", (
        "import sys; sys.exit('WARNING: something\\nERROR: [youtube] abcdefghijk: Sign in to confirm your age')")]
    call(base + "/api/search?q=beatles")
    status, _, body = call(base + "/api/play", {"speaker": "Küche", "ids": [VID]})
    assert status == 502
    assert json.loads(body)["error"] == "Dieses Video lässt sich nicht abspielen: Sign in to confirm your age"
    assert sonos.enqueued == []
    assert playback_error("") == "Dieses Video lässt sich nicht abspielen: unbekannter Fehler"


def test_thumbnails_are_served_from_our_origin_and_cached(srv):
    base, app, _ = srv
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 500
    fetched = []
    app.catalog.thumbnail = lambda vid: fetched.append(vid) or jpeg
    status, headers, body = call(base + f"/thumb/{VID}.jpg")
    assert status == 200 and body == jpeg and headers["Content-Type"] == "image/jpeg"
    assert "max-age" in headers["Cache-Control"]
    assert call(base + f"/thumb/{VID}.jpg")[2] == jpeg
    assert fetched == [VID]  # the second request came from the cache

    def fail(vid):
        raise OSError("offline")

    app.catalog.thumbnail = fail
    assert call(base + "/thumb/bbbbbbbbbbb.jpg")[0] == 404
    assert call(base + "/thumb/../../x.jpg")[0] == 404
    assert call(base + f"/thumb/{VID}.jpg")[0] == 200  # cached copy still served offline


def test_crossfade_and_no_sonos_favourites(srv):
    base, app, sonos = srv
    assert call(base + "/api/sonosfav?speaker=K%C3%BCche")[0] == 404
    assert call(base + "/api/control", {"speaker": "Küche", "action": "crossfade", "value": 1})[0] == 200
    assert sonos.controls[-1] == ("Küche", "crossfade", 1)


def test_lyrics_endpoint(srv):
    base, app, _ = srv
    call(base + "/api/search?q=beatles")
    app.app.lyrics.search = lambda params: [
        {"trackName": "Song", "artistName": "Chan", "duration": 1, "plainLyrics": "la la",
         "syncedLyrics": "[00:00.50] la\n[00:01.00] la"}]
    body = json.loads(call(base + f"/api/lyrics?id={VID}")[2])
    assert body["found"] and body["synced"] == [[0.5, "la"], [1.0, "la"]]
    assert call(base + "/api/lyrics?id=nope")[0] == 400


def test_alarms_fire_once_per_minute_in_their_time_zone(srv):
    from datetime import datetime, timezone

    base, app, sonos = srv
    call(base + "/api/search?q=beatles")
    call(base + "/api/playlist", {"name": "Morgen", "id": VID})
    alarm = {"time": "07:30", "days": [0, 1, 2, 3, 4], "speaker": "Küche",
             "source": {"type": "playlist", "name": "Morgen"}, "volume": 25, "fade": True,
             "tz": "Europe/Berlin"}
    saved = json.loads(call(base + "/api/alarms", {"op": "save", "alarm": alarm})[2])
    assert len(saved) == 1 and saved[0]["enabled"] and saved[0]["id"]
    assert call(base + "/api/alarms", {"op": "save", "alarm": {**alarm, "time": "7:3"}})[0] == 400
    assert call(base + "/api/alarms", {"op": "save", "alarm": {**alarm, "speaker": "Bad"}})[0] == 404
    assert call(base + "/api/alarms", {"op": "save", "alarm": {**alarm, "tz": "Mars/Base"}})[0] == 400

    monday_0630_utc = datetime(2026, 11, 16, 6, 30, tzinfo=timezone.utc)  # 07:30 in Berlin
    assert [a["time"] for a in app.app.alarms.due(monday_0630_utc)] == ["07:30"]
    assert app.app.alarms.due(monday_0630_utc) == []  # once per minute only
    assert app.app.alarms.due(datetime(2026, 11, 21, 6, 30, tzinfo=timezone.utc)) == []  # Saturday
    assert app.app.alarms.due(datetime(2026, 11, 17, 7, 30, tzinfo=timezone.utc)) == []  # 08:30 Berlin

    what = app.app.alarms.fire(saved[0], ramp_seconds=0.15)
    import time
    time.sleep(0.5)
    assert what == "Morgen" and sonos.enqueued[-1][2] == "now"
    volumes = [c[2] for c in sonos.controls if c[1] == "volume"]
    assert volumes[0] == 3 and volumes[-1] == 25  # soft start, then up to the chosen level

    once = json.loads(call(base + "/api/alarms", {"op": "save", "alarm": {
        **alarm, "time": "06:00", "days": [], "source": {"type": "favorites"}, "fade": False}})[2])
    one_off = next(a for a in once if a["time"] == "06:00")
    call(base + "/api/favorite", {"id": VID})
    assert app.app.alarms.fire(one_off) == "Favoriten"
    assert not next(a for a in app.repo.alarms() if a["id"] == one_off["id"])["enabled"]

    assert json.loads(call(base + "/api/alarms", {"op": "test", "id": saved[0]["id"]})[2])["playing"] == "Morgen"
    remaining = json.loads(call(base + "/api/alarms", {"op": "delete", "id": saved[0]["id"]})[2])
    assert [a["time"] for a in remaining] == ["06:00"]


def test_rooms_overview_and_pause_all(srv):
    base, app, sonos = srv
    overview = json.loads(call(base + "/api/system")[2])
    assert [g["name"] for g in overview] == ["Küche", "Bad"]
    assert json.loads(call(base + "/api/pauseall", {})[2]) == {"paused": ["Küche"]}  # only what plays
    assert sonos.controls[-1] == ("Küche", "pause", None)


def test_presets_group_rooms_set_volume_and_play(srv):
    base, app, sonos = srv
    call(base + "/api/search?q=beatles")
    call(base + "/api/playlist", {"name": "Chill", "id": VID})
    sonos.speakers = lambda refresh=False: [{"name": "Küche", "ip": "10.0.0.5", "group": ["Küche"]}]
    sonos.rooms = lambda speaker: [{"name": "Küche", "grouped": True}, {"name": "Bad", "grouped": False}]
    real_get = sonos.get
    sonos.get = lambda name: real_get("Küche") if name in ("Küche", "Bad") else real_get(name)
    preset = {"name": "Abendessen", "rooms": ["Küche", "Bad"], "source": {"type": "playlist", "name": "Chill"},
              "volume": 15}
    saved = json.loads(call(base + "/api/presets", {"op": "save", "preset": preset})[2])
    assert [p["name"] for p in saved] == ["Abendessen"]
    assert json.loads(call(base + "/api/presets")[2])[0]["rooms"] == ["Küche", "Bad"]
    assert call(base + "/api/presets", {"op": "save", "preset": {**preset, "rooms": ["Garage"]}})[0] == 404

    result = json.loads(call(base + "/api/presets", {"op": "run", "id": saved[0]["id"]})[2])
    assert result == {"playing": "Chill", "speaker": "Küche"}
    assert ("Küche", "group", "Bad", True) in sonos.controls  # Bad joins Küche
    assert ("Küche", "volume", 15) in sonos.controls
    assert sonos.enqueued[-1][0] == "Küche" and sonos.enqueued[-1][2] == "now"

    from sonosyt.adapters.json_library import Library
    assert Library(app.repo.path).presets()[0]["name"] == "Abendessen"  # survives a restart
    assert json.loads(call(base + "/api/presets", {"op": "delete", "id": saved[0]["id"]})[2]) == []
    assert call(base + "/api/presets", {"op": "run", "id": saved[0]["id"]})[0] == 400


def test_video_for_the_phone_is_relayed_in_pieces(srv):
    from sonosyt.application.ports import VideoPiece

    base, env, _ = srv
    app = env.app
    data = bytes(range(200))
    seen = []

    class Feed:
        def warm(self, vid):
            seen.append(("warm", vid))

        def open(self, vid, start, end):
            seen.append((vid, start, end))
            if start >= len(data):
                raise ValueError("out of range")
            last = min(len(data) - 1, end if end is not None else start + 49)
            return VideoPiece(start, last, len(data), "video/mp4", iter([data[start:last + 1]]))

    app.streaming.video_feed = Feed()
    status, headers, body = call(base + f"/video/{VID}.mp4", headers={"Range": "bytes=10-"})
    assert status == 206 and body == data[10:60]
    assert headers["Content-Range"] == "bytes 10-59/200" and headers["Content-Type"] == "video/mp4"
    status, _, body = call(base + f"/video/{VID}.mp4")
    assert status == 206 and body == data[:50]
    assert call(base + f"/video/{VID}.mp4", headers={"Range": "bytes=500-"})[0] == 416
    assert call(base + f"/video/{VID}.mp4", method="HEAD")[0] == 200
    assert ("warm", VID) in seen and (VID, 10, None) in seen
    assert call(base + "/video/../../etc.mp4")[0] == 404

    def broken(vid, start, end):
        raise MediaError("Video nicht verfügbar")

    app.streaming.video_feed.open = broken
    status, _, body = call(base + f"/video/{VID}.mp4")
    assert status == 502 and "nicht verfügbar" in json.loads(body)["error"]


def test_charts_on_the_start_screen(srv):
    base, env, _ = srv
    app = env.app
    app.charts.source = type("S", (), {"top_songs": lambda self, c, n: [{"title": "Song", "artist": "Chan", "art": ""}]})()
    app.charts.background = False
    status, _, body = call(base + "/api/charts")
    data = json.loads(body)
    assert status == 200 and data["title"] == "Top-Songs Deutschland"
    assert data["items"][0]["rank"] == 1 and data["items"][0]["query"] == "Chan - Song"


def test_server_update_check(srv):
    base, env, _ = srv
    tools = env.app.system.tools
    env.app.system.version = "0.2.24"
    tools.latest = "0.2.30"
    assert json.loads(call(base + "/api/update")[2]) == {"current": "0.2.24", "latest": "0.2.30", "newer": True}
    tools.latest = "0.2.9"
    assert json.loads(call(base + "/api/update")[2])["newer"] is False  # numbers, not text
    tools.latest = None
    assert json.loads(call(base + "/api/update")[2])["newer"] is False
