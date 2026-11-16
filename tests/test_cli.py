import os

from sonosyt.adapters import cli


def serve_args(monkeypatch, env, argv=()):
    """The options `sonosyt serve` would start with, given these environment variables."""
    for key in [k for k in os.environ if k.startswith("SONOSYT_")]:
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    seen = {}

    def fake_serve(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr(cli, "cmd_serve", fake_serve)
    assert cli.main(["serve", *argv]) == 0
    return seen["args"]


def test_serve_options_come_from_environment_variables(monkeypatch):
    args = serve_args(monkeypatch, {"SONOSYT_SPEAKER_IP": "192.168.20.118, 192.168.20.113", "SONOSYT_PORT": "9000",
                                    "SONOSYT_ALLOW_HOST": "music.example.org", "SONOSYT_BITRATE": "256k"})
    assert args.speaker_ip == ["192.168.20.118", "192.168.20.113"]
    assert args.port == 9000 and args.bitrate == "256k" and args.allow_host == ["music.example.org"]


def test_command_line_wins_over_environment(monkeypatch):
    args = serve_args(monkeypatch, {"SONOSYT_SPEAKER_IP": "10.0.0.1"}, ["--speaker-ip", "10.0.0.9", "--port", "1234"])
    assert args.speaker_ip == ["10.0.0.9"] and args.port == 1234


def test_defaults_without_environment(monkeypatch):
    args = serve_args(monkeypatch, {})
    assert args.speaker_ip is None and args.port == 8327 and args.public_url is None
