import pytest

from sonosyt.sonos import SonosController, SonosError, build_didl
from sonosyt.youtube import Track


class FakeGroup:
    def __init__(self):
        self.members = []
        self.coordinator = None
        self.volume = 33


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
    with pytest.raises(SonosError):
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
    assert ctl.enqueue("Wohnzimmer", ITEMS, play_now=True) == 3
    assert titles(kitchen) == ["a", "b", "One", "Two", "c"] and living.queue == []
    assert kitchen.calls == [("play_from_queue", 2)]


def test_play_now_appends_when_not_playing_from_the_queue(setup):
    ctl, kitchen, _ = setup
    kitchen.queue = ["a"]
    for position in ("0", "NOT_IMPLEMENTED", "7"):
        kitchen.position = position
        kitchen.queue = ["a"]
        kitchen.calls = []
        assert ctl.enqueue("Küche", ITEMS[:1], play_now=True) == 2
        assert titles(kitchen) == ["a", "One"]
        assert kitchen.calls == [("play_from_queue", 1)]


def test_queue_only_appends_without_touching_playback(setup):
    ctl, kitchen, _ = setup
    kitchen.queue = ["a", "b"]
    kitchen.position = "1"
    assert ctl.enqueue("Küche", ITEMS, play_now=False) == 3
    assert titles(kitchen) == ["a", "b", "One", "Two"] and kitchen.calls == []
    with pytest.raises(SonosError):
        ctl.enqueue("Küche", [], play_now=True)


def test_control_seek_queue_and_status(setup):
    ctl, kitchen, living = setup
    ctl.control("Wohnzimmer", "pause")
    ctl.control("Wohnzimmer", "seek", 3725)
    ctl.control("Wohnzimmer", "play_index", 4)
    assert kitchen.calls == ["pause", ("seek", "1:02:05"), ("play_from_queue", 4)]
    ctl.enqueue("Küche", ITEMS, play_now=False)
    ctl.control("Küche", "remove", 0)
    assert ctl.queue("Wohnzimmer") == [{
        "title": "Two", "artist": "YouTube",
        "album_art": "https://i.ytimg.com/vi/bbbbbbbbbbb/hqdefault.jpg",
    }]
    with pytest.raises(SonosError):
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
