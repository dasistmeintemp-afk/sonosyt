"""Play the audio of YouTube videos on Sonos speakers."""

import os

# Set by the Docker image build; a source checkout reports the base version.
__version__ = os.environ.get("SONOSYT_VERSION") or "0.1.0"
