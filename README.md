# sonosyt

Spielt den Ton von YouTube-Videos auf Sonos-Lautsprechern ab.

Sonos kann YouTube-Streams nicht direkt abspielen. `sonosyt` ist deshalb ein kleiner Server
für dein Heimnetz mit passender Android-App: Er holt die Tonspur per [yt-dlp](https://github.com/yt-dlp/yt-dlp),
wandelt sie mit ffmpeg in MP3 um und reicht sie den Lautsprechern über HTTP. Gesteuert wird
über eine Web-Oberfläche (auch am Handy) oder die Kommandozeile.

> Sonos-Lautsprecher geben nur Ton wieder – das Videobild wird nicht übertragen.

## Überblick

- **Server** (dieses Repository): läuft dauerhaft auf einem Gerät im Heimnetz, z. B. einem NAS.
- **Android-App**: Fernbedienung für den Server. Sie zeigt dessen Oberfläche und nimmt Links
  entgegen, die du in der YouTube-App über „Teilen" an sie schickst.

## Server installieren

Der Server muss **im selben Netzwerk wie die Sonos-Lautsprecher** laufen, solange Musik spielt.

### Auf einem NAS mit Docker (empfohlen)

```sh
git clone https://github.com/dasistmeintemp-afk/sonosyt.git
cd sonosyt
docker compose up -d --build
```

Der Container nutzt das Host-Netzwerk (nötig für die Sonos-Suche) und startet nach einem
Neustart des NAS von selbst wieder. Aktualisieren: `git pull && docker compose up -d --build`.

### Direkt mit Python

Benötigt Python 3.10+ und ffmpeg (`sudo apt install ffmpeg` bzw. `brew install ffmpeg`).

```sh
git clone https://github.com/dasistmeintemp-afk/sonosyt.git
cd sonosyt
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/sonosyt serve
```

## Android-App

1. Auf dem Handy die Seite
   [Releases](https://github.com/dasistmeintemp-afk/sonosyt/releases/latest) öffnen und
   `sonosyt.apk` herunterladen
   ([Direktlink](https://github.com/dasistmeintemp-afk/sonosyt/releases/latest/download/sonosyt.apk)).
2. Die Datei öffnen und die Installation erlauben („Aus dieser Quelle zulassen").
3. App starten und **Im Netzwerk suchen** antippen – oder die Adresse des NAS eingeben
   (z. B. `192.168.1.10`).

Danach:

- **Unten** wechselst du zwischen **Start** (Schnellauswahl, zuletzt gespielt, „Für dich",
  Favoriten, Playlists), **Suchen** und **Bibliothek** (Playlists, Favoriten, Verlauf).
- **Der Mini-Player** über der Leiste nimmt die Farbe des laufenden Covers an; ein Tipp darauf
  öffnet die große Wiedergabe-Ansicht mit Lautstärke, Stummschalten und Warteschlange.
- **Suchen und antippen** spielt sofort im gewählten Raum. Beim Tippen erscheinen
  Suchvorschläge; die obersten Treffer werden schon vorbereitet, damit der Ton schnell kommt.
- **Plus-Symbol** reiht den Titel ein. **Drei Punkte** öffnen das Menü: als Nächstes spielen,
  Favorit, zu einer Playlist hinzufügen. Playlists, Favoriten und zuletzt Gespieltes stehen
  auf der Startseite.
- **Teilen → sonosyt** in der YouTube-App spielt das Video direkt ab.
- **Die Lautstärketasten des Handys** regeln die Sonos-Lautstärke – in der App und, solange
  Musik läuft, auch bei gesperrtem Bildschirm (bei gruppierten Räumen die der ganzen Gruppe).
- **Benachrichtigung und Sperrbildschirm** zeigen den laufenden Titel mit Pause und
  Titel vor/zurück. Läuft 15 Minuten nichts, verschwindet die Benachrichtigung wieder.
- **Große Wiedergabe-Ansicht:** Ein Tipp auf den laufenden Titel unten öffnet sie – mit Spulen,
  Lautstärke, Favorit, ähnlichen Titeln und Playlist. Dort läuft das Video stumm und synchron
  zum Ton der Lautsprecher mit (bei Videos, die YouTube nicht einbetten lässt, bleibt das Cover).
- **Unten:** Spulen, Pause, Titel vor/zurück, Lautstärke und die Warteschlange (Titel am
  Griff rechts verschieben, antippen zum Springen). Dort gibt es
  auch Zufall, Wiederholen, Autoplay (nach dem letzten Titel läuft passende Musik weiter)
  und den Sleep-Timer.
- **Räume** (Symbol oben rechts): Räume zusammenschalten und jeden einzeln in der Lautstärke
  regeln.
- **Sonos-Favoriten:** Radiosender und Playlists aus der Sonos-App erscheinen auf „Start" und
  starten mit einem Tipp.
- **Songtexte** (Knopf „Songtext" in der großen Wiedergabe-Ansicht) kommen von
  [LRCLIB](https://lrclib.net); wo vorhanden laufen sie zeilengenau mit, ein Tipp auf eine Zeile
  springt dorthin.
- **Wecker** (Einstellungen → Wecker): zu einer Uhrzeit an gewählten Wochentagen Favoriten, eine
  Playlist oder einen Sonos-Favoriten in einem Raum starten, auf Wunsch sanft lauter werdend.
  Der Server weckt – das Handy muss dafür nicht an sein.
- **Wischen** auf dem Cover oder dem Mini-Player wechselt zum nächsten oder vorherigen Titel;
  **Überblenden** lässt sich in der Warteschlange einschalten.
- **Langes Drücken auf das App-Symbol** bietet „Suchen" und „Bibliothek" direkt an.
- **Lange Videos** (ab 20 Minuten) werden beim nächsten Abspielen dort fortgesetzt, wo du
  aufgehört hast.
- **Lautsprecher-Symbol** neben dem Lautstärkeregler schaltet stumm. Unter **Räume** öffnet das
  Regler-Symbol Bass, Höhen und Loudness je Raum.
- **Playlists speichern:** YouTube-Playlists (Link einfügen → „Speichern") und die aktuelle
  Warteschlange lassen sich als eigene Playlist ablegen.
- **Teilen und „In YouTube öffnen"** stehen im Menü jedes Titels.
- **Verbindung prüfen** in den Einstellungen testet YouTube, Lautsprecher und den Rückweg zum
  Server – hilfreich bei Firewall-Problemen.
- **Livestreams** (z. B. Lofi-Radio) sind in der Suche mit „LIVE" markiert und laufen endlos.
- Die Zurück-Taste schließt geöffnete Fenster und führt von Trefferlisten zur Startseite.
- **Einstellungen** (Zahnrad oben rechts): helles/dunkles Design, Akzentfarbe, Video an/aus und
  Versatz, Verhalten beim Antippen, Schritt der Lautstärketasten, Autoplay, Vibration,
  Verläufe und Zwischenspeicher leeren, Server-Adresse ändern.

Die APK wird bei jeder Änderung unter `android/` automatisch von GitHub Actions gebaut.
Selbst bauen: `cd android && ./gradlew assembleRelease` (benötigt das Android SDK).

## Benutzung ohne App

Im Browser `http://<IP-des-Servers>:8327/` öffnen, Lautsprecher wählen, YouTube-Link
einfügen oder Suchbegriff eingeben und auf **Abspielen** tippen. Playlist-Links liefern alle
Titel der Playlist.

Über die Kommandozeile (der Server muss laufen):

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
| `--speaker-ip IP` | Adresse eines beliebigen Sonos-Lautsprechers vorgeben, falls die automatische Suche nichts findet (z. B. Lautsprecher in einem anderen VLAN); die übrigen Räume werden darüber gefunden |
| `--public-url URL` | Adresse, unter der die Lautsprecher den Server erreichen, falls die automatische Erkennung nicht passt |
| `--cache-dir`, `--cache-size-mb 2048` | Ort und Maximalgröße des MP3-Zwischenspeichers |
| `--bitrate 192k` | MP3-Bitrate |
| `--allow-host NAME` | Zusätzlicher Hostname, unter dem der Server aufgerufen werden darf. IP-Adressen und lokale Namen (z. B. `tower.local`) sind immer erlaubt; fremde Namen werden zum Schutz vor Zugriffen von Webseiten abgelehnt |
| `--auto-update` | yt-dlp beim Start und danach täglich aktualisieren (im Docker-Image eingeschaltet) |

## Wie es funktioniert

1. `/api/play` sucht die Metadaten und reiht pro Video eine URL
   `http://<server>:8327/stream/<video-id>.mp3` in die Sonos-Warteschlange ein.
2. Ruft der Lautsprecher die URL ab, lädt yt-dlp die Tonspur und ffmpeg schreibt sie als MP3
   in den Cache. Ist die Umwandlung beim Abruf schon fertig (bei normalen Musikvideos nach
   wenigen Sekunden), bekommt Sonos eine vollständige Datei – mit Laufzeit und Spulen.
   Andernfalls wird die noch wachsende Datei live durchgereicht; Spulen geht dann erst beim
   nächsten Abspielen.

## Aktualisieren

**Server:** ein Befehl im Ordner des Repositorys. Er holt den neuen Stand, baut das Image und
startet den Container mit denselben Optionen und demselben Cache wie zuvor:

```sh
bash update.sh
```

Die Bedienoberfläche kommt vom Server – die meisten Neuerungen sind damit sofort in der App,
ohne neue APK. Wer es automatisch möchte, lässt den Befehl regelmäßig laufen (auf Unraid z. B.
mit dem Plugin „User Scripts").

**App:** Eine neue APK ist nur nötig, wenn sich an der App selbst etwas ändert. Die App zeigt
dann einen Hinweis mit Download-Knopf. Vollautomatisch geht es mit
[Obtainium](https://github.com/ImranR98/Obtainium): dort die Adresse dieses Repositorys
eintragen, dann installiert Obtainium neue Versionen von selbst.

## Sonos in einem anderen VLAN

Die automatische Suche funktioniert nur innerhalb eines Netzwerks. Stehen die Lautsprecher in
einem anderen VLAN als der Server, in `docker-compose.yml` die Zeile `command:` aktivieren und
die IP eines Lautsprechers eintragen, dann `docker compose up -d`. Zusätzlich muss die Firewall
zwischen den VLANs erlauben:

- Server → Lautsprecher: TCP-Port 1400 (Steuerung)
- Lautsprecher → Server: TCP-Port 8327 (Abruf der Musik)

## Hinweise

- Der Server hat **keine Anmeldung**: Jeder im Netzwerk kann ihn bedienen. Nicht ins Internet
  freigeben.
- YouTube ändert sich häufig. Der Docker-Container hält yt-dlp deshalb selbst aktuell
  (beim Start und einmal täglich). Ohne Docker: `sonosyt serve --auto-update` oder von Hand
  `.venv/bin/pip install -U yt-dlp`.
- Favoriten, Verlauf und Autoplay-Einstellung liegen in `library.json` im Cache-Verzeichnis.
- Die APK ist mit einem Schlüssel signiert, der offen in diesem Repository liegt
  (`android/sonosyt.keystore`), damit Updates sich ohne Deinstallation einspielen lassen.
  Installiere deshalb nur APKs aus diesem Repository.
- Bitte die Nutzungsbedingungen von YouTube und das Urheberrecht beachten.

## Entwicklung

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```
