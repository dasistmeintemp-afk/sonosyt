"""Failures the application reports to its users."""


class SpeakerError(Exception):
    """A room is unknown, unreachable or refused a command."""


class MediaError(Exception):
    """A video or playlist cannot be found, fetched or played."""
