"""The domain rules on their own: no server, no fakes, no I/O."""

from datetime import datetime, timezone

import pytest

from sonosyt.domain.alarm import normalize_alarm, occurrence, ramp, start_volume
from sonosyt.domain.playback import (
    likely_picks, pick_autoplay, playback_error, position_to_remember, tracks_to_play, upcoming_video,
    wants_autoplay,
)
from sonosyt.domain.track import Track, is_url, watch_id

A, B, C = Track("aaaaaaaaaaa", "A", duration=200), Track("bbbbbbbbbbb", "B", duration=7200), Track(
    "ccccccccccc", "C", live=True)


def test_track_ids_and_urls():
    assert watch_id("aaaaaaaaaaa") == "aaaaaaaaaaa"
    for bad in ("short", "../etc/passw", "aaaaaaaaaaa.mp3"):
        with pytest.raises(ValueError):
            watch_id(bad)
    assert is_url("https://youtu.be/x") and not is_url("lofi beats")
    assert Track.from_dict(A.to_dict()) == A


def test_what_a_search_plays_and_converts_ahead():
    assert tracks_to_play([A, B], "lofi") == [A]
    assert tracks_to_play([A, B], "https://www.youtube.com/playlist?list=PL1") == [A, B]
    assert likely_picks([B, C, A]) == ["aaaaaaaaaaa"]  # not the two-hour video, not the livestream


def test_playback_error_is_one_readable_line():
    detail = "WARNING: x\nERROR: [youtube] abcdefghijk: Sign in to confirm your age\nmore"
    assert playback_error(detail) == "Dieses Video lässt sich nicht abspielen: Sign in to confirm your age"


@pytest.mark.parametrize("status, expected", [
    ({"state": "PLAYING", "video_id": "v", "duration": 6000, "position": 2590}, ("v", 2590)),
    ({"state": "PLAYING", "video_id": "v", "duration": 6000, "position": 5990}, ("v", 0)),  # finished
    ({"state": "PLAYING", "video_id": "v", "duration": 6000, "position": 10}, None),  # just started
    ({"state": "PLAYING", "video_id": "v", "duration": 300, "position": 200}, None),  # short clip
    ({"state": "PAUSED_PLAYBACK", "video_id": "v", "duration": 6000, "position": 2590}, None),
    ({"state": "PLAYING", "video_id": "v", "duration": 6000, "position": 2590, "live": True}, None),
])
def test_which_positions_are_remembered(status, expected):
    assert position_to_remember(status) == expected


def test_autoplay_rules():
    last = {"state": "PLAYING", "video_id": "v", "queue_position": 3, "queue_length": 3}
    assert wants_autoplay(last, None)
    assert not wants_autoplay(last, "v")  # already handled this track
    assert not wants_autoplay({**last, "queue_position": 2}, None)  # more is queued
    assert not wants_autoplay({**last, "repeat": 1}, None)
    assert pick_autoplay([C, A, B], {"aaaaaaaaaaa"}) == B  # fresh, never live
    assert pick_autoplay([C, A], {"aaaaaaaaaaa"}) == A  # everything heard: still something
    assert pick_autoplay([C], set()) is None


def test_upcoming_video():
    status = {"state": "PLAYING", "queue_position": 1, "queue_length": 2}
    assert upcoming_video(status, [{"video_id": "a"}, {"video_id": "b"}]) == "b"
    assert upcoming_video(status, [{"video_id": "a"}, {"video_id": "b", "live": True}]) is None
    assert upcoming_video({**status, "queue_position": 2}, [{}, {}]) is None


ALARM = {"time": "07:30", "days": [0, 1, 2, 3, 4], "speaker": "Küche", "source": {"type": "favorites"},
         "volume": 25, "tz": "Europe/Berlin"}


def test_alarm_validation():
    alarm = normalize_alarm(ALARM)
    assert alarm["enabled"] and alarm["fade"] and alarm["id"]
    for change in ({"time": "7:30"}, {"days": [7]}, {"speaker": ""}, {"source": {"type": "?"}},
                   {"source": {"type": "playlist"}}, {"tz": "Mars/Base"}, {"id": "../x"}):
        with pytest.raises(ValueError):
            normalize_alarm({**ALARM, **change})
    assert normalize_alarm({**ALARM, "volume": 500})["volume"] == 100


def test_alarm_occurrence_in_its_time_zone():
    alarm = normalize_alarm(ALARM)
    monday = datetime(2026, 11, 16, 6, 30, tzinfo=timezone.utc)  # 07:30 in Berlin
    assert occurrence(alarm, monday) == "2026-11-16 07:30"
    assert occurrence(alarm, datetime(2026, 11, 21, 6, 30, tzinfo=timezone.utc)) is None  # Saturday
    assert occurrence(alarm, datetime(2026, 11, 16, 7, 30, tzinfo=timezone.utc)) is None  # 08:30 there
    assert occurrence({**alarm, "enabled": False}, monday) is None
    assert occurrence({**alarm, "days": []}, datetime(2026, 11, 21, 6, 30, tzinfo=timezone.utc))  # once


def test_soft_start():
    alarm = normalize_alarm(ALARM)
    assert start_volume(alarm) == 3 and start_volume({**alarm, "fade": False}) == 25
    levels = ramp(3, 25)
    assert levels[-1] == 25 and levels == sorted(levels) and ramp(25, 25) == []


def test_sources_and_presets():
    from sonosyt.domain.preset import normalize_preset
    from sonosyt.domain.source import describe, normalize_source

    assert normalize_source({"type": "playlist", "name": "Chill"}) == {"type": "playlist", "name": "Chill"}
    # Sonos favourites are gone; alarms and presets that used one play the favourites
    assert normalize_source({"type": "sonosfav", "index": 2, "title": "Radio"}) == {"type": "favorites"}
    assert describe({"type": "favorites"}) == "Favoriten"
    for bad in (None, {}, {"type": "playlist"}, {"type": "spotify"}):
        with pytest.raises(ValueError):
            normalize_source(bad)
    preset = normalize_preset({"name": "  Abend  essen ", "rooms": ["Küche", "Bad", "Küche", ""],
                               "source": {"type": "favorites"}, "volume": "15"})
    assert preset["name"] == "Abend essen" and preset["rooms"] == ["Küche", "Bad"] and preset["volume"] == 15
    assert normalize_preset({**preset, "volume": None})["volume"] is None  # keep the room's volume
    for change in ({"name": ""}, {"rooms": []}, {"source": {"type": "?"}}, {"id": "a/b"}):
        with pytest.raises(ValueError):
            normalize_preset({**preset, **change})
