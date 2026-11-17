import pytest

from sonosyt.application.artists import FRESH, ArtistService
from sonosyt.application.tracks import TrackCatalog
from sonosyt.domain.music import artist_name, artist_songs, by_artist, looks_like_song, music_only
from sonosyt.domain.track import Track


def T(i, title, uploader="Chan", duration=200, live=False):
    return Track(f"{i:a<11}"[:11], title, uploader, duration, live)


def test_what_counts_as_a_song():
    assert looks_like_song(T("a", "Ed Sheeran - Perfect (Official Video)"))
    assert not looks_like_song(T("b", "Perfect", duration=30))
    assert not looks_like_song(T("c", "Best of 2026 Mix", duration=3600))
    assert not looks_like_song(T("d", "Radio", live=True))
    assert not looks_like_song(T("e", "Producer reacts to Perfect"))
    assert looks_like_song(T("f", "Perfect Karaoke"), "perfect karaoke")   # asked for it
    assert looks_like_song(T("c", "Rooftop Live (full set)", duration=6000), "fred again live")
    tracks = [T("g", "Podcast #12", duration=300), T("h", "Song")]
    assert [t.title for t in music_only(tracks)] == ["Song"]
    assert music_only(tracks[:1]) == tracks[:1]   # never empty


def test_artist_name_and_matching():
    assert artist_name(T("a", "Perfect", "Ed Sheeran - Topic")) == "Ed Sheeran"
    assert artist_name(T("b", "Apache 207 feat. X - Komet (Official Video)", "Apache 207")) == "Apache 207"
    assert artist_name(T("c", "Perfect (Official Video)", "EdSheeranVEVO")) == "EdSheeran"
    assert by_artist(T("d", "Perfect", "Ed Sheeran - Topic"), "Ed Sheeran")
    assert by_artist(T("e", "Ed Sheeran - Perfect", "Some Lyrics Channel"), "ed sheeran")
    assert not by_artist(T("f", "Perfect (Cover)", "Cover Girl"), "Ed Sheeran")


def test_artist_page_keeps_their_songs_once():
    tracks = [T("a", "Ed Sheeran - Perfect (Official Video)", "Ed Sheeran"),
              T("b", "Perfect", "Ed Sheeran - Topic"),
              T("c", "Interview with Ed Sheeran", "TV", 900),
              T("d", "Shape of You", "Ed Sheeran - Topic"),
              T("e", "Ed Sheeran Perfect Piano Tutorial", "Piano"),
              T("f", "Best Ed Sheeran songs (lyrics)", "Fan", 220),
              T("g", "Unrelated", "Someone")]
    assert [t.id[0] for t in artist_songs(tracks, "Ed Sheeran")] == ["a", "d", "f"]


class Catalog:
    def __init__(self, found, newest=None):
        self.found, self.newest, self.calls = found, newest or {}, []

    def lookup(self, query, limit=10):
        self.calls.append((query, limit))
        return self.found

    def latest(self, query, limit=10):
        self.calls.append(("latest", query))
        if query == "Broken":
            from sonosyt.domain.errors import MediaError
            raise MediaError("down")
        return self.newest.get(query, [])

    def related(self, vid):
        return []


class Converter:
    def prefetch(self, ids):
        pass


class Library:
    def __init__(self):
        self.followed = []

    def tracks(self):
        return []

    def artists(self):
        return list(self.followed)

    def set_follow(self, name, on):
        self.followed = [a for a in self.followed if a != name] + ([name] if on else [])


def test_music_search_filters_terms_but_not_links():
    catalog = Catalog([T("a", "Song"), T("b", "Song (10 hours)", duration=36000), T("c", "Song reaction")])
    tracks = TrackCatalog(catalog, Converter(), Library())
    assert [t.id[0] for t in tracks.search("song", music=True)] == ["a"]
    assert catalog.calls[-1] == ("song", 20)   # asks for more, since some are dropped
    assert len(tracks.search("https://youtu.be/xyz", music=True)) == 3
    assert len(tracks.search("song")) == 3


def test_new_songs_of_followed_artists():
    now = [1000.0]
    newest = {"Mara": [T("m1", "Ozean", "Mara - Topic"), T("m2", "Mara - Gold", "Mara"), T("x1", "Ozean (Cover)", "Fan")],
              "Rico": [T("r1", "Fieber", "Rico - Topic")]}
    library = Library()
    artists = ArtistService(TrackCatalog(Catalog([], newest), Converter(), library), library,
                            clock=lambda: now[0], background=False)
    assert artists.new_songs()["items"] == []
    artists.follow("Mara", True)
    artists.follow("Rico", True)
    artists.follow("Broken", True)
    data = artists.new_songs()
    assert [i["id"][:2] for i in data["items"]] == ["m1", "r1", "m2"]   # each artist's newest first
    assert data["items"][0]["artist"] == "Mara" and data["pending"] == []
    calls = len(artists.tracks.catalog.calls)
    artists.new_songs()
    assert len(artists.tracks.catalog.calls) == calls   # remembered
    now[0] += FRESH + 1
    artists.new_songs()
    assert len(artists.tracks.catalog.calls) == calls + 3


def test_artist_page_reports_following():
    library = Library()
    library.followed = ["Ed Sheeran"]
    artists = ArtistService(TrackCatalog(Catalog([T("a", "Perfect", "Ed Sheeran - Topic")]), Converter(), library),
                            library, background=False)
    page = artists.page("ed sheeran")
    assert page["followed"] and page["tracks"][0]["title"] == "Perfect"
    with pytest.raises(ValueError):
        artists.page("  ")
