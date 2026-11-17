import subprocess

from sonosyt.adapters import updater


def test_update_reports_new_version(monkeypatch):
    versions = iter(["2026.01.01", "2026.02.02"])
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, next(versions) + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(updater.subprocess, "run", run)
    assert updater.update_ytdlp() is True
    pip = calls[1]
    assert pip[1:4] == ["-m", "pip", "install"] and "--upgrade" in pip and pip[-1] == "yt-dlp[default]"


def test_update_failure_is_not_fatal(monkeypatch):
    def run(cmd, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "2026.01.01\n", "")
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: no network")

    monkeypatch.setattr(updater.subprocess, "run", run)
    assert updater.update_ytdlp() is False


def test_latest_server_version_reads_ghcr_tags():
    import io
    import json

    from sonosyt.adapters.updater import latest_server_version

    seen = []

    def opener(req):
        url = getattr(req, "full_url", req)
        seen.append((url, getattr(req, "headers", {})))
        if "token" in url:
            return io.BytesIO(json.dumps({"token": "abc"}).encode())
        return io.BytesIO(json.dumps({"tags": ["latest", "0.2.9", "0.2.25", "0.2.100", "sha-1"]}).encode())

    assert latest_server_version("me/app", opener) == "0.2.100"
    assert seen[1][1].get("Authorization") == "Bearer abc"

    def offline(req):
        raise OSError("down")

    assert latest_server_version("me/app", offline) is None
