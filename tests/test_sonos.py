import pytest

from sonosyt.adapters.sonos import SonosController, build_didl
from sonosyt.domain.errors import SpeakerError
from sonosyt.domain.track import Track


class FakeGroup:
    def __init__(self):
        self.members = []
        self.coordinator = None
        self.volume = 33
        self.mute = False


class FakeSpeaker:
    def __init__(self, name, ip):
        self.player_name = name
        self.ip_address = ip
        self.is_visible = True
        self.volume = 20
        self.queue = []
        self.calls = []
        self.group = FakeGroup()
        self.group.members = [self]
        self.group.coordinator = self

        self.position = "0"
        self.play_mode = "NORMAL"
        self.shuffle = False
        self.repeat = False
        self.sleep = None
        self.joined = "unset"
        self.mute = False
        self.bass, self.treble, self.loudness = 0, 0, True
        self.uid = "RINCON_" + ip.replace(".", "")

    def get_sleep_timer(self):
        return self.sleep

    def set_sleep_timer(self, seconds):
        self.sleep = seconds

    def join(self, master):
        self.joined = master

    def unjoin(self):
        self.joined = None

    @property
    def queue_size(self):
        return len(self.queue)

    def add_to_queue(self, item, position=0):
        if position:
            self.queue.insert(position - 1, item)
            return position
        self.queue.append(item)
        return len(self.queue)

    def get_queue(self, max_items=100):
        return list(self.queue)

    def seek(self, timestamp):
        self.calls.append(("seek", timestamp))

    def remove_from_queue(self, index):
        self.queue.pop(index)

    def move_in_queue(self, start, new_pos):
        if not 0 <= new_pos < len(self.queue):
            raise ValueError("out of range")
        self.queue.insert(new_pos, self.queue.pop(start))

    def play_from_queue(self, index):
        self.calls.append(("play_from_queue", index))

    def pause(self):
        self.calls.append("pause")

    def get_current_track_info(self):
        return {"title": "T", "artist": "A", "position": "0:00:05", "duration": "1:03:00",
                "album_art": "", "playlist_position": self.position,
                "uri": "http://10.0.0.2:8327/stream/aaaaaaaaaaa.mp3"}

    def get_current_transport_info(self):
        return {"current_transport_state": "PLAYING"}


@pytest.fixture
def setup(monkeypatch):
    kitchen, living = FakeSpeaker("Küche", "10.0.0.5"), FakeSpeaker("Wohnzimmer", "10.0.0.6")
    # Wohnzimmer is grouped under Küche.
    living.group = kitchen.group
    kitchen.group.members = [kitchen, living]
    ctl = SonosController()
    monkeypatch.setattr(ctl, "_discover", lambda: [living, kitchen])
    return ctl, kitchen, living


def test_lists_speakers_sorted_with_groups(setup):
    ctl, _, _ = setup
    assert ctl.speakers() == [
        {"name": "Küche", "ip": "10.0.0.5", "group": ["Küche", "Wohnzimmer"]},
        {"name": "Wohnzimmer", "ip": "10.0.0.6", "group": ["Küche", "Wohnzimmer"]},
    ]


def test_get_by_name_or_ip(setup):
    ctl, kitchen, living = setup
    assert ctl.get("küche") is kitchen
    assert ctl.get("10.0.0.6") is living
    with pytest.raises(SpeakerError):
        ctl.get("Bad")


ITEMS = [
    (Track("aaaaaaaaaaa", "One", "Chan", 200), "http://10.0.0.2:8327/stream/aaaaaaaaaaa.mp3"),
    (Track("bbbbbbbbbbb", "Two"), "http://10.0.0.2:8327/stream/bbbbbbbbbbb.mp3"),
]


def titles(speaker):
    return [i if isinstance(i, str) else i.title for i in speaker.queue]


def test_play_now_inserts_after_current_track_on_the_coordinator(setup):
    ctl, kitchen, living = setup
    kitchen.queue = ["a", "b", "c"]
    kitchen.position = "2"  # "b" is playing
    assert ctl.enqueue("Wohnzimmer", ITEMS, "now") == 3
    assert titles(kitchen) == ["a", "b", "One", "Two", "c"] and living.queue == []
    assert kitchen.calls == [("play_from_queue", 2)]


def test_play_now_appends_when_not_playing_from_the_queue(setup):
    ctl, kitchen, _ = setup
    kitchen.queue = ["a"]
    for position in ("0", "NOT_IMPLEMENTED", "7"):
        kitchen.position = position
        kitchen.queue = ["a"]
        kitchen.calls = []
        assert ctl.enqueue("Küche", ITEMS[:1], "now") == 2
        assert titles(kitchen) == ["a", "One"]
        assert kitchen.calls == [("play_from_queue", 1)]


def test_queue_only_appends_without_touching_playback(setup):
    ctl, kitchen, _ = setup
    kitchen.queue = ["a", "b"]
    kitchen.position = "1"
    assert ctl.enqueue("Küche", ITEMS, "queue") == 3
    assert titles(kitchen) == ["a", "b", "One", "Two"] and kitchen.calls == []
    with pytest.raises(SpeakerError):
        ctl.enqueue("Küche", [], "now")


def test_control_seek_queue_and_status(setup):
    ctl, kitchen, living = setup
    ctl.control("Wohnzimmer", "pause")
    ctl.control("Wohnzimmer", "seek", 3725)
    ctl.control("Wohnzimmer", "play_index", 4)
    assert kitchen.calls == ["pause", ("seek", "1:02:05"), ("play_from_queue", 4)]
    ctl.enqueue("Küche", ITEMS, "queue")
    ctl.control("Küche", "remove", 0)
    assert ctl.queue("Wohnzimmer") == [{
        "title": "Two", "artist": "YouTube",
        "album_art": "https://i.ytimg.com/vi/bbbbbbbbbbb/hqdefault.jpg",
        "video_id": "bbbbbbbbbbb", "live": False,
    }]
    with pytest.raises(SpeakerError):
        ctl.control("Küche", "explode")
    kitchen.position = "2"
    status = ctl.status("Wohnzimmer")
    assert status["state"] == "PLAYING" and status["title"] == "T"
    assert (status["position"], status["duration"], status["queue_position"]) == (5, 3780, 2)
    assert status["video_id"] == "aaaaaaaaaaa"


def test_volume_of_grouped_speaker_is_the_group_volume(setup):
    ctl, kitchen, living = setup
    assert ctl.status("Wohnzimmer")["volume"] == 33
    ctl.set_volume("Wohnzimmer", 250)
    assert kitchen.group.volume == 100 and (kitchen.volume, living.volume) == (20, 20)


def test_volume_of_single_speaker(setup):
    ctl, kitchen, _ = setup
    kitchen.group.members = [kitchen]
    ctl.set_volume("Küche", -5)
    assert kitchen.volume == 0 and kitchen.group.volume == 33
    assert ctl.status("Küche")["volume"] == 0


def test_didl_metadata():
    from soco.data_structures import to_didl_string

    item = build_didl(Track("aaaaaaaaaaa", "Rock & <Roll>", "Chan", 3725), "http://h/stream/aaaaaaaaaaa.mp3")
    xml = to_didl_string(item)
    assert "Rock &amp; &lt;Roll&gt;" in xml
    assert 'duration="1:02:05"' in xml
    assert 'protocolInfo="http-get:*:audio/mpeg:*"' in xml
    assert "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg" in xml
    assert item.resources[0].uri == "http://h/stream/aaaaaaaaaaa.mp3"


def test_fixed_ip_expands_to_all_rooms(monkeypatch):
    import soco

    kitchen, living = FakeSpeaker("Küche", "10.0.0.5"), FakeSpeaker("Wohnzimmer", "10.0.0.6")
    kitchen.visible_zones = {kitchen, living}

    class Unreachable(FakeSpeaker):
        @property
        def visible_zones(self):
            raise OSError("unreachable")

    dead = Unreachable("Bad", "10.0.0.9")
    monkeypatch.setattr(soco, "SoCo", lambda ip: {"10.0.0.5": kitchen, "10.0.0.9": dead}[ip])
    assert [s["name"] for s in SonosController(ips=["10.0.0.5"]).speakers()] == ["Küche", "Wohnzimmer"]
    # A speaker that cannot report the system is still listed as given.
    names = [s["name"] for s in SonosController(ips=["10.0.0.9", "10.0.0.5"]).speakers()]
    assert names == ["Bad", "Küche", "Wohnzimmer"]


def test_shuffle_repeat_and_sleep_timer(setup):
    ctl, kitchen, _ = setup
    ctl.control("Wohnzimmer", "shuffle", 1)
    ctl.control("Wohnzimmer", "repeat", 2)
    assert kitchen.shuffle is True and kitchen.repeat == "ONE"
    ctl.control("Küche", "repeat", 0)
    assert kitchen.repeat is False
    with pytest.raises(SpeakerError):
        ctl.control("Küche", "repeat", 7)
    ctl.control("Küche", "sleep", 30)
    assert kitchen.sleep == 1800 and ctl.status("Küche")["sleep"] == 1800
    ctl.control("Küche", "sleep", 0)
    assert kitchen.sleep is None and ctl.status("Küche")["sleep"] == 0
    kitchen.play_mode = "SHUFFLE_REPEAT_ONE"
    status = ctl.status("Küche")
    assert (status["shuffle"], status["repeat"]) == (True, 2)
    kitchen.play_mode = "SHUFFLE_NOREPEAT"
    assert (ctl.status("Küche")["shuffle"], ctl.status("Küche")["repeat"]) == (True, 0)


def test_rooms_grouping_and_single_room_volume(setup, monkeypatch):
    ctl, kitchen, living = setup
    bath = FakeSpeaker("Bad", "10.0.0.7")
    monkeypatch.setattr(ctl, "_discover", lambda: [living, kitchen, bath])
    ctl.devices(refresh=True)
    assert ctl.rooms("Wohnzimmer") == [
        {"name": "Bad", "bass": 0, "treble": 0, "loudness": True, "volume": 20,
         "grouped": False, "selected": False},
        {"name": "Küche", "bass": 0, "treble": 0, "loudness": True, "volume": 20,
         "grouped": True, "selected": False},
        {"name": "Wohnzimmer", "bass": 0, "treble": 0, "loudness": True, "volume": 20,
         "grouped": True, "selected": True},
    ]
    ctl.group("Wohnzimmer", "Bad", True)
    assert bath.joined is kitchen  # joins the coordinator of the selected room's group
    ctl.group("Wohnzimmer", "Küche", False)
    assert kitchen.joined is None
    with pytest.raises(SpeakerError):
        ctl.group("Küche", "Küche", False)
    ctl.set_volume("Wohnzimmer", 40, single=True)
    assert living.volume == 40 and kitchen.group.volume == 33


def test_live_tracks_have_no_duration_and_are_recognised(setup):
    from soco.data_structures import to_didl_string

    ctl, kitchen, _ = setup
    item = build_didl(Track("ccccccccccc", "Radio", "Chan", None, live=True), "http://h/live/ccccccccccc.mp3")
    assert "duration=" not in to_didl_string(item)
    kitchen.get_current_track_info = lambda: {"uri": "http://h/live/ccccccccccc.mp3", "playlist_position": "1"}
    status = ctl.status("Küche")
    assert status["live"] is True and status["video_id"] == "ccccccccccc"


def test_play_next_inserts_without_interrupting_and_queue_can_be_reordered(setup):
    ctl, kitchen, _ = setup
    kitchen.queue = ["a", "b", "c"]
    kitchen.position = "1"
    assert ctl.enqueue("Küche", ITEMS[:1], "next") == 2
    assert titles(kitchen) == ["a", "One", "b", "c"] and kitchen.calls == []
    ctl.control("Küche", "move", 3, 0)
    assert titles(kitchen) == ["c", "a", "One", "b"]
    ctl.control("Küche", "move", 0, 2)
    assert titles(kitchen) == ["a", "One", "c", "b"]
    with pytest.raises(SpeakerError):
        ctl.control("Küche", "move", 0, 9)
    with pytest.raises(SpeakerError):
        ctl.enqueue("Küche", ITEMS, "sideways")


def test_grouping_falls_back_to_the_websocket_api_when_upnp_is_refused(setup, monkeypatch):
    import sonos_websocket

    ctl, kitchen, living = setup
    bath = FakeSpeaker("Bad", "10.0.0.7")
    monkeypatch.setattr(ctl, "_discover", lambda: [living, kitchen, bath])
    ctl.devices(refresh=True)

    def refuse(master=None):
        raise RuntimeError("UPnP Error 1001 received")

    bath.join = bath.unjoin = refuse
    sent = []
    answer = {"success": True}

    class FakeWebsocket:
        def __init__(self, ip):
            sent.append(("connect", ip))

        async def connect(self):
            pass

        async def close(self):
            sent.append("close")

        async def get_household_id(self):
            return "HH"

        async def get_groups(self):
            return [{"success": True}, {"groups": [
                {"id": "G-other", "coordinatorId": "RINCON_x", "playerIds": ["RINCON_x"]},
                {"id": "G-kitchen", "coordinatorId": kitchen.uid,
                 "playerIds": [kitchen.uid, living.uid, bath.uid]},
            ]}]

        async def send_command(self, command, options=None):
            sent.append((command, options))
            return [answer, {"reason": "nope"}]

    monkeypatch.setattr(sonos_websocket, "SonosWebsocket", FakeWebsocket)
    ctl.group("Wohnzimmer", "Bad", True)
    assert sent == [
        ("connect", "10.0.0.5"),  # asked of the group's coordinator
        ({"namespace": "groups:1", "command": "modifyGroupMembers", "householdId": "HH",
          "groupId": "G-kitchen"}, {"playerIdsToAdd": [bath.uid]}),
        "close",
    ]
    sent.clear()
    attempts = []
    bath.unjoin = lambda: attempts.append("upnp")
    ctl.group("Wohnzimmer", "Bad", False)
    assert sent[1][1] == {"playerIdsToRemove": [bath.uid]}
    assert attempts == []  # remembered: the refused UPnP attempt is skipped from now on

    answer["success"] = False  # both ways refused: a readable error, not a crash
    with pytest.raises(SpeakerError, match="Bad lässt sich gerade nicht hinzufügen"):
        ctl.group("Wohnzimmer", "Bad", True)


def test_mute_and_tone_controls(setup):
    ctl, kitchen, living = setup
    ctl.control("Wohnzimmer", "mute", 1)
    assert kitchen.group.mute is True and living.mute is False  # grouped: the whole group
    assert ctl.status("Wohnzimmer")["muted"] is True
    ctl.control("Wohnzimmer", "mute", 0)
    assert ctl.status("Wohnzimmer")["muted"] is False
    assert ctl.set_eq("Wohnzimmer", bass=25, treble=-3, loudness=False) == {
        "bass": 10, "treble": -3, "loudness": False}
    assert (living.bass, living.treble, living.loudness) == (10, -3, False) and kitchen.bass == 0
    assert ctl.set_eq("Küche") == {"bass": 0, "treble": 0, "loudness": True}  # nothing given: unchanged


def test_sonos_favourites_play_radio_directly_and_playlists_via_queue(setup):
    ctl, kitchen, living = setup

    class Ref:
        def __init__(self, item_class, uri):
            self.item_class, self.uri = item_class, uri

        def get_uri(self):
            return self.uri

    class Fav:
        def __init__(self, title, ref, art=""):
            self.title, self._ref, self.album_art_uri = title, ref, art
            self.description, self.resource_meta_data = "Radiosender", "<meta/>"

        @property
        def reference(self):
            if self._ref is None:
                raise ValueError("no resMD")
            return self._ref

    favs = [Fav("Radio Eins", Ref("object.item.audioItem.audioBroadcast", "x-sonosapi-stream:s1"), "/getaa?x"),
            Fav("Party-Mix", Ref("object.container.playlistContainer", "x-rincon-cpcontainer:1")),
            Fav("Kaputt", None)]
    kitchen.music_library = type("ML", (), {"get_sonos_favorites": lambda self, complete_result: favs})()
    played = []
    kitchen.play_uri = lambda uri, meta, title="": played.append((uri, meta, title))

    listed = ctl.favorites("Wohnzimmer")  # grouped: asks the coordinator
    assert [f["title"] for f in listed] == ["Radio Eins", "Party-Mix", "Kaputt"]
    assert listed[0]["art"] == "http://10.0.0.5:1400/getaa?x"

    assert ctl.play_favorite("Wohnzimmer", 0) == "Radio Eins"
    assert played == [("x-sonosapi-stream:s1", "<meta/>", "Radio Eins")]
    kitchen.queue = ["a"]
    assert ctl.play_favorite("Küche", 1) == "Party-Mix"
    assert kitchen.queue[-1] is favs[1]._ref and kitchen.calls[-1] == ("play_from_queue", 1)
    with pytest.raises(SpeakerError, match="Kaputt"):
        ctl.play_favorite("Küche", 2)
    with pytest.raises(SpeakerError):
        ctl.play_favorite("Küche", 9)


def test_crossfade_switch_and_status(setup):
    ctl, kitchen, _ = setup
    kitchen.cross_fade = False
    ctl.control("Wohnzimmer", "crossfade", 1)
    assert kitchen.cross_fade is True and ctl.status("Küche")["crossfade"] is True
