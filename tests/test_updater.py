import subprocess

from sonosyt import updater


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
