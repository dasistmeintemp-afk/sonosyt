"""Composition root: builds the application from its adapters.

The only place that knows which technology implements which port. Tests pass
fakes for any of them.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import __version__
from .application.alarms import AlarmService
from .application.app import Application
from .application.artists import ArtistService
from .application.background import AutoplayService, Monitor
from .application.charts import ChartService
from .application.library import LibraryService
from .application.lyrics import LyricsService
from .application.playback import PlaybackService
from .application.presets import PresetService
from .application.rooms import RoomService
from .application.sources import SourcePlayer
from .application.system import StreamingService, SystemService
from .application.tracks import TrackCatalog


def build(
    cache_dir: str | os.PathLike,
    *,
    port: int = 8327,
    public_url: str | None = None,
    speaker_ips: list[str] | None = None,
    bitrate: str = "192k",
    cache_size_mb: int = 2048,
    prefetch_wait: float = 8.0,
    auto_update: bool = False,
    speakers=None,
    catalog=None,
    converter=None,
    library=None,
    lyrics_source=None,
    network=None,
    tools=None,
    thumbnails=None,
    video_feed=None,
    chart_source=None,
    charts_country: str = "de",
    normalize: bool = True,
) -> Application:
    cache_dir = Path(cache_dir)
    if speakers is None:
        from .adapters.sonos import SonosController

        speakers = SonosController(ips=speaker_ips)
    if catalog is None:
        from .adapters.youtube import YtDlpCatalog

        heal = None
        if auto_update:  # yt-dlp may update itself: do so at once when YouTube changed
            from .adapters.updater import update_ytdlp

            heal = update_ytdlp
        catalog = YtDlpCatalog(heal=heal)
    if converter is None:
        from .adapters.transcoder import Transcoder

        converter = Transcoder(cache_dir, bitrate=bitrate, max_cache_bytes=cache_size_mb * 1024**2,
                               normalize=normalize)
    if library is None:
        from .adapters.json_library import Library

        cache_dir.mkdir(parents=True, exist_ok=True)
        library = Library(cache_dir / "library.json")
    if lyrics_source is None:
        from .adapters.lrclib import LrclibSource

        lyrics_source = LrclibSource()
    if network is None:
        from .adapters.network import SystemNetwork

        network = SystemNetwork()
    if tools is None:
        from .adapters.updater import YtDlpTools

        tools = YtDlpTools()
    if thumbnails is None:
        from .adapters.thumbnails import DiskThumbnailCache

        thumbnails = DiskThumbnailCache(cache_dir / "thumbs", lambda vid: catalog.thumbnail(vid))

    if video_feed is None:
        from .adapters.videofeed import YouTubeVideoFeed

        video_feed = YouTubeVideoFeed()

    if chart_source is None:
        from .adapters.apple_charts import AppleCharts

        chart_source = AppleCharts()

    tracks = TrackCatalog(catalog, converter, library)
    playback = PlaybackService(speakers, converter, tracks, library, network,
                               port=port, public_url=public_url, prefetch_wait=prefetch_wait)
    rooms = RoomService(speakers, playback)
    sources = SourcePlayer(library, speakers, playback)
    alarms = AlarmService(library, speakers, playback, rooms, sources)
    autoplay = AutoplayService(speakers, tracks, library, playback)
    streaming = StreamingService(converter, thumbnails, video_feed)
    return Application(
        tracks=tracks,
        playback=playback,
        rooms=rooms,
        library=LibraryService(library, tracks, rooms),
        alarms=alarms,
        presets=PresetService(library, speakers, rooms, sources),
        lyrics=LyricsService(lyrics_source),
        autoplay=autoplay,
        monitor=Monitor(speakers, converter, library, playback, autoplay, alarms),
        streaming=streaming,
        system=SystemService(__version__, speakers, converter, network, tools, playback, streaming),
        charts=ChartService(chart_source, tracks, charts_country),
        artists=ArtistService(tracks, library),
    )


def check_ffmpeg(ffmpeg: str = "ffmpeg") -> None:
    if not shutil.which(ffmpeg):
        raise SystemExit(
            "ffmpeg wurde nicht gefunden. Bitte installieren "
            "(z. B. `sudo apt install ffmpeg` oder `brew install ffmpeg`)."
        )
