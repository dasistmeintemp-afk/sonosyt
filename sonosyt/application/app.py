"""Everything the inbound adapters (HTTP, CLI) may use, in one place."""

from __future__ import annotations

from dataclasses import dataclass

from .alarms import AlarmService
from .background import AutoplayService, Monitor
from .charts import ChartService
from .library import LibraryService
from .lyrics import LyricsService
from .playback import PlaybackService
from .presets import PresetService
from .rooms import RoomService
from .system import StreamingService, SystemService
from .tracks import TrackCatalog


@dataclass
class Application:
    tracks: TrackCatalog
    playback: PlaybackService
    rooms: RoomService
    library: LibraryService
    alarms: AlarmService
    presets: PresetService
    lyrics: LyricsService
    autoplay: AutoplayService
    monitor: Monitor
    streaming: StreamingService
    system: SystemService
    charts: ChartService
