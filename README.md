# sonosyt

Spielt den Ton von YouTube-Videos auf Sonos-Lautsprechern ab.

Sonos kann YouTube-Streams nicht direkt abspielen. `sonosyt` ist deshalb ein kleiner Server
für dein Heimnetz: Er holt die Tonspur per [yt-dlp](https://github.com/yt-dlp/yt-dlp),
wandelt sie mit ffmpeg in MP3 um und reicht sie den Lautsprechern über HTTP. Gesteuert wird
über eine Web-Oberfläche (auch am Handy) oder die Kommandozeile.

> Sonos-Lautsprecher geben nur Ton wieder – das Videobild wird nicht übertragen.

## Voraussetzungen

- Ein Rechner, der **im selben Netzwerk wie die Sonos-Lautsprecher** läuft, solange Musik
  spielt (PC, Raspberry Pi, NAS …)
- Python 3.10 oder neuer
- ffmpeg (`sudo apt install ffmpeg` bzw. `brew install ffmpeg`)

## Installation

```sh
git clone https://github.com/dasistmeintemp-afk/sonosyt.git
cd sonosyt
python3 -m venv .venv
.venv/bin/pip install .
```

## Benutzung

Server starten:

```sh
.venv/bin/sonosyt serve
```

Dann im Browser `http://<IP-des-Rechners>:8327/` öffnen, Lautsprecher wählen, YouTube-Link
einfügen oder Suchbegriff eingeben und auf **Abspielen** tippen. Playlist-Links liefern alle
Titel der Playlist.

Alternativ über die Kommandozeile (der Server muss laufen):

```sh
sonosyt speakers                                   # Lautsprecher auflisten
sonosyt play -s Wohnzimmer https://youtu.be/…      # Video abspielen
sonosyt play -s Wohnzimmer lofi hip hop            # ersten Suchtreffer abspielen
sonosyt play -s Wohnzimmer -q https://youtube.com/playlist?list=…   # Playlist einreihen
sonosyt control -s Wohnzimmer pause                # play | pause | stop | next | previous | clear
sonosyt control -s Wohnzimmer volume 25
```

Die Titel landen in der normalen Sonos-Warteschlange, lassen sich also auch in der Sonos-App
sehen, überspringen und pausieren.

### Optionen von `sonosyt serve`

| Option | Bedeutung |
| --- | --- |
| `--port 8327` | Port des Servers |
| `--speaker-ip IP` | Lautsprecher fest vorgeben, falls die automatische Suche nichts findet (z. B. in Docker oder über VLAN-Grenzen); mehrfach möglich |
| `--public-url URL` | Adresse, unter der die Lautsprecher den Server erreichen, falls die automatische Erkennung nicht passt |
| `--cache-dir`, `--cache-size-mb 2048` | Ort und Maximalgröße des MP3-Zwischenspeichers |
| `--bitrate 192k` | MP3-Bitrate |

## Wie es funktioniert

1. `/api/play` sucht die Metadaten und reiht pro Video eine URL
   `http://<server>:8327/stream/<video-id>.mp3` in die Sonos-Warteschlange ein.
2. Ruft der Lautsprecher die URL ab, lädt yt-dlp die Tonspur und ffmpeg schreibt sie als MP3
   in den Cache. Ist die Umwandlung beim Abruf schon fertig (bei normalen Musikvideos nach
   wenigen Sekunden), bekommt Sonos eine vollständige Datei – mit Laufzeit und Spulen.
   Andernfalls wird die noch wachsende Datei live durchgereicht; Spulen geht dann erst beim
   nächsten Abspielen.

## Hinweise

- Der Server hat **keine Anmeldung**: Jeder im Netzwerk kann ihn bedienen. Nicht ins Internet
  freigeben.
- Funktioniert ein Video plötzlich nicht mehr, hilft meist ein Update von yt-dlp:
  `.venv/bin/pip install -U yt-dlp`.
- Bitte die Nutzungsbedingungen von YouTube und das Urheberrecht beachten.

## Entwicklung

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```
