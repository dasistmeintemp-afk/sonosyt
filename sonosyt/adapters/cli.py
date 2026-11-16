"""Command line (inbound adapter): ``sonosyt serve | speakers | play | control``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8327


def _default_cache_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(base) / "sonosyt")


def _call(server: str, path: str, payload: dict | None = None):
    """Talk to a running ``sonosyt serve``."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        server.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc)["error"]
        except Exception:  # noqa: BLE001
            message = str(exc)
        raise SystemExit(f"Fehler: {message}")
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Server {server} nicht erreichbar ({exc.reason}). Läuft `sonosyt serve`?"
        )


def cmd_serve(args) -> int:
    from ..bootstrap import build, check_ffmpeg
    from .http.server import make_server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    check_ffmpeg()
    app = build(
        args.cache_dir,
        port=args.port,
        public_url=args.public_url,
        speaker_ips=args.speaker_ip,
        bitrate=args.bitrate,
        cache_size_mb=args.cache_size_mb,
    )
    server = make_server(app, args.host, args.port, args.allow_host, args.public_url)
    app.monitor.start()
    if args.auto_update:
        from .updater import start_auto_update

        start_auto_update()
    print(f"sonosyt läuft: http://localhost:{app.playback.port}/  (Strg+C zum Beenden)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


def cmd_speakers(args) -> int:
    speakers = _call(args.server, "/api/speakers?refresh=1")
    if not speakers:
        print("Keine Sonos-Lautsprecher gefunden.", file=sys.stderr)
        return 1
    for s in speakers:
        group = f"  (Gruppe: {', '.join(s['group'])})" if len(s["group"]) > 1 else ""
        print(f"{s['name']:<24} {s['ip']}{group}")
    return 0


def cmd_play(args) -> int:
    result = _call(
        args.server,
        "/api/play",
        {"speaker": args.speaker, "query": " ".join(args.query),
         "mode": "queue" if args.queue else "now"},
    )
    verb = "Eingereiht" if args.queue else "Spiele"
    for t in result["queued"]:
        print(f"{verb}: {t['title']}" + (f" – {t['uploader']}" if t["uploader"] else ""))
    return 0


def cmd_control(args) -> int:
    payload = {"speaker": args.speaker}
    if args.action == "volume":
        if args.value is None:
            raise SystemExit("Bitte Lautstärke angeben, z. B.: sonosyt control -s Küche volume 25")
        payload["volume"] = args.value
    else:
        payload["action"] = args.action
    _call(args.server, "/api/control", payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sonosyt", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def client(p):
        p.add_argument(
            "--server",
            default=os.environ.get("SONOSYT_SERVER", f"http://localhost:{DEFAULT_PORT}"),
            help="Adresse des laufenden sonosyt-Servers (Standard: %(default)s)",
        )

    # Every serve option can also come from an environment variable, so container
    # managers such as Unraid can offer them as plain input fields.
    env = os.environ.get

    def env_list(name):
        return [part.strip() for part in (env(name) or "").replace(";", ",").split(",") if part.strip()] or None

    p = sub.add_parser("serve", help="Server mit Web-Oberfläche starten")
    p.add_argument("--host", default=env("SONOSYT_HOST") or "0.0.0.0", help="Bind-Adresse (Standard: %(default)s)")
    p.add_argument("--port", type=int, default=int(env("SONOSYT_PORT") or DEFAULT_PORT))
    p.add_argument(
        "--speaker-ip", action="append", metavar="IP",
        help="Sonos-IP fest vorgeben statt automatischer Suche (mehrfach möglich; "
        "Umgebungsvariable SONOSYT_SPEAKER_IP, mehrere durch Komma getrennt)",
    )
    p.add_argument(
        "--public-url", metavar="URL", default=env("SONOSYT_PUBLIC_URL") or None,
        help="Adresse, unter der die Lautsprecher diesen Server erreichen "
        "(Standard: automatisch, http://<LAN-IP>:<port>)",
    )
    p.add_argument(
        "--allow-host", action="append", metavar="NAME",
        help="zusätzlicher Hostname, unter dem der Server aufgerufen werden darf "
        "(IP-Adressen und lokale Namen wie tower.local sind immer erlaubt)",
    )
    p.add_argument("--cache-dir", default=env("SONOSYT_CACHE_DIR") or _default_cache_dir())
    p.add_argument("--cache-size-mb", type=int, default=int(env("SONOSYT_CACHE_SIZE_MB") or 2048))
    p.add_argument("--bitrate", default=env("SONOSYT_BITRATE") or "192k", help="MP3-Bitrate (Standard: %(default)s)")
    p.add_argument(
        "--auto-update", action="store_true",
        help="yt-dlp beim Start und danach täglich per pip aktualisieren",
    )
    p.set_defaults(func=lambda args: cmd_serve(args))

    p = sub.add_parser("speakers", help="Sonos-Lautsprecher auflisten")
    client(p)
    p.set_defaults(func=cmd_speakers)

    p = sub.add_parser("play", help="Video-URL, Playlist-URL oder Suchbegriff abspielen")
    client(p)
    p.add_argument("-s", "--speaker", required=True, help="Raumname oder IP")
    p.add_argument("-q", "--queue", action="store_true", help="nur einreihen, nicht sofort spielen")
    p.add_argument("query", nargs="+")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("control", help="Wiedergabe steuern")
    client(p)
    p.add_argument("-s", "--speaker", required=True, help="Raumname oder IP")
    p.add_argument("action", choices=["play", "pause", "stop", "next", "previous", "clear", "volume"])
    p.add_argument("value", nargs="?", type=int, help="Lautstärke 0-100 (nur bei volume)")
    p.set_defaults(func=cmd_control)

    args = parser.parse_args(argv)
    if args.command == "serve":
        args.speaker_ip = args.speaker_ip or env_list("SONOSYT_SPEAKER_IP")
        args.allow_host = args.allow_host or env_list("SONOSYT_ALLOW_HOST")
    return args.func(args)
