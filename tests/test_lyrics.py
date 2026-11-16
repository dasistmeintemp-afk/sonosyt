from types import SimpleNamespace

from sonosyt.application.lyrics import LyricsService
from sonosyt.domain.lyrics import best_match, parse_synced, split_title
from sonosyt.domain.track import Track


def Lyrics(search):
    return LyricsService(SimpleNamespace(search=search))


def test_split_title():
    assert split_title("Daft Punk - Get Lucky (Official Video) [4K]", "DaftPunkVEVO") == ("Daft Punk", "Get Lucky")
    assert split_title("Get Lucky", "Daft Punk - Topic") == ("Daft Punk", "Get Lucky")
    assert split_title("Adele – Hello (Live at the BBC) | 2015", "") == ("Adele", "Hello (Live at the BBC)")


def test_synced_parsing_and_best_match():
    assert parse_synced("[00:12.30] Hello\n[01:02.00]\nnot a line") == [(12.3, "Hello"), (62.0, "")]
    results = [
        {"duration": 400, "syncedLyrics": "[00:01.00] wrong recording"},
        {"duration": 241, "plainLyrics": "plain only"},
        {"duration": 243, "syncedLyrics": "[00:01.00] right"},
    ]
    assert best_match(results, 240)["syncedLyrics"] == "[00:01.00] right"
    assert best_match([], 240) is None


def test_lookup_falls_back_and_caches():
    calls = []

    def search(params):
        calls.append(params)
        return [] if "artist_name" in params else [{"duration": 200, "plainLyrics": "words"}]

    lyrics = Lyrics(search)
    track = Track("aaaaaaaaaaa", "Artist - Song (Official Audio)", "Chan", 200)
    first = lyrics.get(track)
    assert first["found"] and first["plain"] == "words" and first["synced"] == []
    assert calls == [{"track_name": "Song", "artist_name": "Artist"}, {"q": "Artist Song"}]
    assert lyrics.get(track) is first and len(calls) == 2

    def offline(params):
        raise OSError("no network")

    assert Lyrics(offline).get(track)["found"] is False
