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

### Unraid (empfohlen)

sonosyt gibt es als fertiges Image (`ghcr.io/dasistmeintemp-afk/sonosyt`). Unraid zeigt damit im
Docker-Tab wie bei anderen Apps an, wenn ein Update bereitsteht, und aktualisiert mit einem Klick.

1. Vorlage einmalig herunterladen (im Unraid-Terminal):
   ```sh
   wget -O /boot/config/plugins/dockerMan/templates-user/my-sonosyt.xml \
     https://raw.githubusercontent.com/dasistmeintemp-afk/sonosyt/ccr-1d7e2f0e-vpigs0/unraid/sonosyt.xml
   ```
2. **Docker → Add Container**, oben bei *Template* „sonosyt" wählen.
3. Bei **Sonos-Lautsprecher (IP)** die Adresse eines Lautsprechers eintragen, falls die Lautsprecher
   in einem anderen VLAN sind; sonst leer lassen. **Apply**.

Danach: Symbol im Docker-Tab → **WebUI** öffnet die Oberfläche. Steht dort „update ready",
genügt **Apply Update**. Mit dem Plugin „CA Auto Update Applications" geht das auch automatisch.

### Anderes NAS mit Docker

```sh
docker run -d --name sonosyt --restart unless-stopped --network host \
  -v /pfad/zu/sonosyt-daten:/cache \
  -e SONOSYT_SPEAKER_IP=192.168.1.50 \
  ghcr.io/dasistmeintemp-afk/sonosyt:latest
```

Oder aus dem Quellcode: `docker compose up -d --build` im geklonten Repository.

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
- **Teilen** in der YouTube-App: „Auf Sonos abspielen" oder „Zur Sonos-Warteschlange" – du bleibst
  in YouTube, eine kurze Meldung bestätigt es.
- **Die Lautstärketasten des Handys** regeln die Sonos-Lautstärke – in der App und, solange
  Musik läuft, auch bei gesperrtem Bildschirm (bei gruppierten Räumen die der ganzen Gruppe).
- **Benachrichtigung und Sperrbildschirm** zeigen den laufenden Titel mit Pause und
  Titel vor/zurück. Läuft 15 Minuten nichts, verschwindet die Benachrichtigung wieder.
- **Große Wiedergabe-Ansicht:** Ein Tipp auf den laufenden Titel unten öffnet sie – mit Spulen,
  Lautstärke, Favorit, ähnlichen Titeln und Playlist. Dort läuft das Video stumm und synchron
  zum Ton der Lautsprecher mit – bei **jedem** Video, auch bei Musikvideos, die YouTube nicht
  einbetten lässt: Das Bild kommt über deinen Server. Bild und Ton liegen auf Bruchteile einer
  Sekunde beieinander; wer es noch genauer will, stellt unter Einstellungen → Video-Versatz nach.
  **Handy quer halten** zeigt das Video im ganzen Bildschirm, ein Tipp blendet die Steuerung ein.
- **Mini-Player:** Ein Ring um den Play-Knopf zeigt wie bei Sonos, wie weit der Titel ist; unter dem
  Titel steht die Restzeit („noch 2:31 · Künstler“).
- **Unten:** Spulen, Pause, Titel vor/zurück, Lautstärke und die Warteschlange (Titel am
  Griff rechts verschieben, antippen zum Springen). Dort gibt es
  auch Zufall, Wiederholen, Autoplay (nach dem letzten Titel läuft passende Musik weiter)
  und den Sleep-Timer.
- **Räume gruppieren direkt beim Abspielen:** In der großen Wiedergabe-Ansicht zeigt der Knopf
  unten (bzw. „Spielt in …" oben) den Raum samt Gruppe, z. B. „Familienzimmer +1". Ein Tipp öffnet
  die Raumauswahl: Räume ankreuzen (oder „Alle"), dann **Übernehmen** – erst dann wird gruppiert.
  Die Lautstärke je Raum lässt sich dort sofort regeln. Auch der Mini-Player hat dafür einen
  Lautsprecher-Knopf, der die Anzahl der Räume in der Gruppe zeigt.
- **Große Wiedergabe-Ansicht:** Stern zum Merken direkt neben dem Titel, Zufall und Wiederholen
  neben den Wiedergabetasten, ⋯ für alle Aktionen zum Titel (z. B. „Ähnliche Titel"); ein Tipp
  auf die Gesamtdauer zeigt stattdessen die Restzeit.
- **Räume** (untere Leiste): jeder Raum als Karte mit dem, was läuft, Play/Pause und Lautstärke;
  ein Tipp auf den Namen wählt ihn aus, „Alle pausieren" stoppt überall.
- **Voreinstellungen** (oben auf „Start"): Musik, Räume und Lautstärke zusammen speichern und mit
  einem Tipp starten – der erste Raum spielt, die anderen werden dazugeschaltet. Lange drücken
  bearbeitet eine Voreinstellung.
- **Nur Musik:** Die Suche findet Songs und Musikvideos – Podcasts, Reaktionsvideos, Tutorials,
  stundenlange Mixe und Livestreams bleiben draußen (außer man sucht danach, z. B. „… live" oder
  „… karaoke"). Ein Tipp auf „Nur Musik" über den Ergebnissen zeigt wieder alle Videos. Auch
  „Ähnliche Titel", „Für dich" und Autoplay bleiben bei Musik.
- **Radio zu einem Titel** (Menü ⋯ eines Titels): spielt den Titel und danach ähnliche Songs;
  Autoplay wird für den Raum eingeschaltet, so geht es endlos weiter.
- **Künstler:** Ein Tipp auf den Künstlernamen in der großen Wiedergabe-Ansicht oder „Mehr von …"
  im Menü zeigt seine Songs (eigene Uploads zuerst, jeder Song nur einmal) mit „Abspielen" und
  „Zufällig". Mit **Folgen** erscheinen neue Songs des Künstlers auf „Start" unter „Neu von
  deinen Künstlern"; die Liste aller gefolgten Künstler steht in der Bibliothek.
- **Gleiche Lautstärke:** Der Server gleicht beim Umwandeln die Lautheit aller Titel an (wie
  Streamingdienste), damit auf ein leises Video kein viel zu lautes folgt. Abschalten mit
  `SONOSYT_NORMALIZE=0`. Gilt für neu umgewandelte Titel – wer alle angleichen will, leert
  einmal den Zwischenspeicher (Einstellungen).
- **Charts** (auf „Start"): die aktuell meistgehörten Songs (Deutschland, täglich aktuell) als
  Leiste mit Platzierung – ein Tipp spielt den Song, „Alle" die ganze Liste. Der Server sucht
  jeden Song auf YouTube und bevorzugt dabei das offizielle Video bzw. den Song selbst statt
  Live-Mitschnitten, Mixen oder Karaoke. Die Liste stammt aus dem öffentlichen Charts-Feed von
  Apple Music (YouTube hat seine Trends-Seite 2025 abgeschafft); das Land lässt sich mit
  `SONOSYT_CHARTS_COUNTRY` (z. B. `at`, `ch`) ändern.
- **Songtexte** (Knopf „Songtext" in der großen Wiedergabe-Ansicht) kommen von
  [LRCLIB](https://lrclib.net); wo vorhanden laufen sie zeilengenau mit, ein Tipp auf eine Zeile
  springt dorthin.
- **Wecker** (Einstellungen → Wecker): zu einer Uhrzeit an gewählten Wochentagen Favoriten oder eine
  Playlist in einem Raum starten, auf Wunsch sanft lauter werdend.
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
| `--port 8327` | Port des Servers (alle Optionen gehen auch als Umgebungsvariable, z. B. `SONOSYT_PORT`, `SONOSYT_SPEAKER_IP`, `SONOSYT_PUBLIC_URL`, `SONOSYT_BITRATE`, `SONOSYT_CHARTS_COUNTRY`, `SONOSYT_NORMALIZE`) |
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
3. Das Bild für die große Wiedergabe-Ansicht holt die App unter `/video/<video-id>.mp4`: yt-dlp
   sucht einen Videostream ohne Ton (bis 720p) und der Server reicht ihn stückweise durch. Weil
   Sonos die Position nur in ganzen Sekunden meldet, schätzt die App aus mehreren Meldungen den
   genauen Startzeitpunkt und gleicht kleine Abweichungen über die Abspielgeschwindigkeit aus.

## Aktualisieren

**Server auf Unraid:** über den Docker-Tab („Apply Update"), siehe oben.

**Prüfen, ob es Neues gibt:** In der App unter Einstellungen → „Nach Updates suchen“. Dort
stehen die installierte und die neueste Version von App und Server; eine neue App installiert
sich mit „Aktualisieren“ direkt.

**Server aus dem Quellcode:** ein Befehl im Ordner des Repositorys. Er holt den neuen Stand, baut das Image und
startet den Container mit denselben Optionen und demselben Cache wie zuvor:

```sh
bash update.sh
```

Die Bedienoberfläche kommt vom Server – die meisten Neuerungen sind damit sofort in der App,
ohne neue APK. Wer es automatisch möchte, lässt den Befehl regelmäßig laufen (auf Unraid z. B.
mit dem Plugin „User Scripts").

**App:** Eine neue APK ist nur nötig, wenn sich an der App selbst etwas ändert. Die App zeigt
dann oben „Neue App-Version verfügbar"; **Aktualisieren** lädt sie und öffnet direkt die
Installation (beim ersten Mal fragt Android, ob sonosyt Apps installieren darf). Vollautomatisch geht es mit
[Obtainium](https://github.com/ImranR98/Obtainium): dort die Adresse dieses Repositorys
eintragen, dann installiert Obtainium neue Versionen von selbst.

## Sonos in einem anderen VLAN

Die automatische Suche funktioniert nur innerhalb eines Netzwerks. Stehen die Lautsprecher in
einem anderen VLAN als der Server, in `docker-compose.yml` die Zeile `command:` aktivieren und
die IP eines Lautsprechers eintragen, dann `docker compose up -d`. Zusätzlich muss die Firewall
zwischen den VLANs erlauben:

- Server → Lautsprecher: TCP-Port 1400 (Steuerung)
- Lautsprecher → Server: TCP-Port 8327 (Abruf der Musik)

## Kleinigkeiten, die das Leben leichter machen

- Ist der Server kurz weg (Neustart, Update), verbindet sich die App von selbst wieder; nach einem
  Server-Update lädt die Oberfläche automatisch neu.
- Ändert YouTube etwas und yt-dlp scheitert deshalb, aktualisiert sich yt-dlp sofort selbst und
  versucht es erneut (höchstens einmal pro Stunde).
- Der Container meldet seinen Zustand – Unraid zeigt „healthy" an.
- Im Browser am Computer: Leertaste Pause, ↑/↓ Lautstärke, N/P nächster/vorheriger Titel,
  M stumm, / Suche, Esc schließt.

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

## Architektur

Der Server ist hexagonal aufgebaut (Ports & Adapters). Die Fachlogik kennt weder Sonos noch
YouTube, ffmpeg, Dateien oder HTTP; sie spricht nur über Schnittstellen („Ports") nach außen.

```
sonosyt/
  domain/          Fachregeln ohne Ein-/Ausgabe: Track, Wecker, Abspiel-Regeln, Songtext-Auswahl
  application/     Anwendungsfälle (Abspielen, Räume, Bibliothek, Wecker, Autoplay …)
    ports.py       Schnittstellen nach außen: Lautsprecher, Video-Katalog, Umwandlung, Bibliothek …
  adapters/        Technik an den Rändern
    sonos.py         SoCo                    → SpeakerSystem
    youtube.py       yt-dlp                  → VideoCatalog
    videofeed.py     yt-dlp + HTTP           → VideoFeed (Bild fürs Handy)
    apple_charts.py  Apple-Charts-Feed       → ChartSource
    transcoder.py    yt-dlp + ffmpeg         → AudioConverter
    json_library.py  JSON-Datei              → LibraryRepository
    lrclib.py, thumbnails.py, network.py, updater.py
    http/            Web-Server und Oberfläche (eingehend)
    cli.py           Kommandozeile (eingehend)
  bootstrap.py     Composition Root: verbindet Ports mit Adaptern
```

`tests/test_architecture.py` prüft bei jedem Testlauf, dass Domain und Anwendung keine Adapter
oder Fremdbibliotheken importieren. Für Tests werden die Adapter über `bootstrap.build(...)`
durch Fakes ersetzt.

## Entwicklung

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```
