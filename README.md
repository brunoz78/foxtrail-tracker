# Foxtrail-Tracker

Kleine, selbst gehostete Web-App, um den Überblick zu behalten, welche
[Foxtrails](https://foxtrail.ch) man in der Schweiz schon gemacht hat – mit
Datum, Anzahl Mitspieler und Bemerkung, für mehrere Benutzer mit Login.

Die Trail-Liste wird automatisch mit foxtrail.ch abgeglichen. Dabei geht nichts
verloren: Trails, die es nicht mehr gibt, werden nie gelöscht – bereits gemachte
bleiben in der Liste, noch offene wandern in ein Archiv.

> Dieses Projekt ist privat und **nicht** mit der Foxtrail AG verbunden. Die
> Trail-Daten (Name, Ort, Route, Dauer, Preis, Bewertung) stammen von der
> öffentlichen Übersicht auf foxtrail.ch und gehören der Foxtrail AG.

## Funktionen

* Liste aller aktuell angebotenen Trails mit Filter (alle / offen / gemacht) und
  Volltextsuche; jede Spalte sortierbar (Ort, Trail, Region, Typ, Bewertung, Dauer,
  Preis, Datum), Mehrfach-Filter für Region, Typ und Dauer direkt im Spaltenkopf
* Schwierigkeitsgrad (Einfach / Mittel / Schwierig) laut foxtrail.ch als Spalte,
  sortier- und filterbar; GO-Trails haben keinen
* Typ als Badge wie auf foxtrail.ch: **Foxtrail**, **MINI**, **MAXI**, **GO** – abgeleitet
  aus dem Trail-Namen bzw. dem GO-Badge (das MINI/MAXI-Badge ist auf der Website nur
  ins Bild gezeichnet); sortiert nach Region erscheinen Gruppen-Zeilen
* Pro Trail: **gemacht**, **Datum**, **Anzahl Mitspieler**, **Bemerkung**
  (ein Eintrag je Trail, gemeinsam für alle Benutzer)
* **Spalten wählbar** (Knopf „Spalten“ in der Liste, gespeichert pro Benutzer): zusätzlich
  Startort, Zielort, Start- und Zielzeit, Team-Code, Bestellnummer, „Neu ab“ und „Erfasst von“
* **Kachelansicht** als zweite Ansicht der Liste: drei Kacheln pro Reihe (auf dem Handy
  eine) mit dem eigenen Foto oder dem Titelbild von foxtrail.ch, Klick öffnet den Trail;
  Sortierung und Filter wie in der Liste, die gewählte Ansicht bleibt in der Sitzung
* **Fotos** pro Trail: hochladen, ersetzen, löschen (JPEG/PNG/WebP bis 15 MB). Beim
  Speichern wird das Bild gedreht, auf höchstens 2560 px verkleinert und ohne
  EXIF-Daten (also ohne GPS-Position) abgelegt. In der Liste erscheint eine Vorschau
* **Statistik**: gemacht von gesamt (davon im laufenden Jahr), erkundete Regionen, Spielzeit gesamt und im Schnitt,
  schnellster und längster Trail, Trails pro Jahr sowie Fortschritt je Region, Typ und
  Schwierigkeit (ein Klick auf einen Balken öffnet die passende Liste)
* Manuell erfasste Trails für früher gemachte Trails, die nicht mehr angeboten werden
* Abgleich mit foxtrail.ch – wöchentlich per systemd-Timer und per Knopfdruck
* Archiv: nicht mehr angebotene, noch nicht gemachte Trails
* Mehrere Benutzer mit Login (gehashte Passwörter, Sperre nach 5 Fehlversuchen),
  Administratoren verwalten Benutzer, lösen den Abgleich aus und importieren Bestellungen
* Keine externen Abhängigkeiten im Browser (kein CDN), hell/dunkel automatisch
* **Auf dem Smartphone** bedienbar: Menü hinter ☰, Liste und Archiv als kompakte
  Karten statt breiter Tabelle, Sortier- und Filterleiste oben, grosse Tipp-Flächen

## Abgleich-Regeln

| Situation | Ergebnis |
|---|---|
| Trail neu auf foxtrail.ch | wird angelegt (offen), in der Liste dauerhaft „Neu ab MM/JJ“; Filter **Neu** zeigt alle, neueste zuerst |
| Trail schon im Seed, aber laut [Neuigkeiten](https://foxtrail.ch/thema/neuigkeiten/) kürzlich eröffnet | „Neu ab“ aus dem Seed (`neu_seit`), `foxtrailctl seed` trägt es auch in bestehende Datenbanken nach |
| Trail weiterhin gelistet | Metadaten (Preis, Dauer, Bewertung, Route) aktualisiert – eigene Einträge bleiben |
| Trail nicht mehr gelistet, **bereits gemacht** | bleibt in der Hauptliste, markiert „nicht mehr im Angebot seit MM/JJ“ |
| Trail nicht mehr gelistet, **noch offen** | erscheint im **Archiv** |
| Archivierter Trail wird als gemacht markiert | wandert zurück in die Hauptliste |
| Archivierter Trail taucht wieder auf | wird reaktiviert |
| Manuell erfasster Trail | wird vom Abgleich nie verändert |

Schlüssel für den Abgleich ist der URL-Pfad des Trails auf foxtrail.ch (z. B.
`wallis/allalin-maxi`), nicht der Name. Liefert der Scraper weniger als die Hälfte
der bisher bekannten Trails (Website-Umbau, Störung), bricht der Abgleich ab und
verändert nichts.

## Installation im Proxmox-LXC

### Per Script (empfohlen)

Ein Aufruf auf der **Proxmox-Host-Shell** legt einen Debian-13-Container an,
installiert die App nach `/opt/foxtrail-tracker`, befüllt die Trail-Liste,
legt einen Administrator mit zufälligem Passwort an und richtet den
wöchentlichen Abgleich ein:

```bash
COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/brunoz78/ProxmoxVED/main bash -c "$(curl -fsSL https://raw.githubusercontent.com/brunoz78/ProxmoxVED/main/ct/foxtrail-tracker.sh)"
```

Benutzername und Passwort werden am Ende angezeigt und liegen im Container in
`~/foxtrail-tracker.creds`. Eigene Zugangsdaten vorab: `var_admin_user=…`
und `var_admin_pass=…` vor den Aufruf setzen. Spätere Updates laufen mit
`update` im Container; installiert wird immer das neueste GitHub-Release.

Das Script nutzt das Framework von [community-scripts](https://community-scripts.org),
liegt aber in einem eigenen Fork und ist **nicht** Teil der offiziellen
Sammlung. Aufbau und Hintergründe: [`proxmox/README.md`](proxmox/README.md).

### Testen vor dem Release

Das Script installiert immer das neueste GitHub-Release. Um einen Stand aus
`main` (oder einem anderen Branch) vorher im Test-Container auszuprobieren,
dort als root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/brunoz78/foxtrail-tracker/main/deploy/dev-update.sh)
```

Optional mit Branch-Namen als Argument. Datenbank und Konfiguration bleiben
erhalten; `update` setzt später wieder das neueste Release ein.

### Von Hand im Container

Getestet mit Debian 12/13 (unprivilegierter Container, 512 MB RAM reichen).

```bash
# im Container, als root
apt-get install -y git
git clone https://github.com/brunoz78/foxtrail-tracker.git /root/foxtrail-tracker
cd /root/foxtrail-tracker
bash deploy/install.sh
foxtrailctl create-user admin --admin      # Passwort wird abgefragt
```

Danach ist die App unter `http://<container-ip>:8080/` erreichbar. `install.sh`
legt an:

| Pfad | Zweck |
|---|---|
| `/opt/foxtrail-tracker` | Code und Python-venv |
| `/var/lib/foxtrail-tracker/foxtrail.db` | SQLite-Datenbank (alles, was euch gehört) |
| `/etc/foxtrail-tracker.env` | Konfiguration, `SECRET_KEY` wird zufällig erzeugt |
| `foxtrail.service` | Web-App (gunicorn, Dienstbenutzer `foxtrail`) |
| `foxtrail-sync.timer` | Abgleich jeden Montag 04:30 |
| `/usr/local/bin/foxtrailctl` | Verwaltungs-CLI mit Produktionskonfiguration |

**Aktualisieren:** `cd /root/foxtrail-tracker && git pull && bash deploy/install.sh`
(Datenbank und Konfiguration bleiben erhalten).

**Reverse-Proxy / HTTPS:** Hinter nginx, Caddy oder Traefik läuft die App ohne weitere
Einstellungen. Zwei optionale Absicherungen in `/etc/foxtrail-tracker.env`, danach
`systemctl restart foxtrail`:

* `FORCE_HTTPS=1` – das Login-Cookie wird nur noch über HTTPS gesendet. Nur setzen, wenn
  ausschliesslich über den Proxy mit HTTPS zugegriffen wird: Über `http://<IP>:8080`
  funktioniert die Anmeldung danach nicht mehr.
* `BIND=127.0.0.1:8080` – die App ist nur noch im Container selbst erreichbar. Nur
  sinnvoll, wenn der Proxy **im selben Container** läuft. Läuft er woanders (eigener LXC,
  Nginx Proxy Manager, …), `BIND=0.0.0.0:8080` lassen und den direkten Zugriff bei Bedarf
  per Firewall auf die IP des Proxys beschränken (z. B. Proxmox-Firewall des LXC).

**Backup:** Die Datei `/var/lib/foxtrail-tracker/foxtrail.db` sichern (oder
`foxtrailctl export-json backup.json`).

## Verwaltung (CLI)

```bash
foxtrailctl create-user NAME [--admin]   # Benutzer anlegen
foxtrailctl set-password NAME
foxtrailctl list-users
foxtrailctl sync                         # Abgleich jetzt (macht der Timer sonst wöchentlich)
foxtrailctl stats
foxtrailctl export-json [datei]          # komplette Liste inkl. eigener Einträge
foxtrailctl import-excel liste.xlsx      # "Gemacht?"-Spalte aus der Excel-Übersicht übernehmen
foxtrailctl import-bestellungen datei    # eigene Bestellungen von foxtrail.ch übernehmen (s. unten)
journalctl -u foxtrail -u foxtrail-sync  # Logs
systemctl list-timers foxtrail-sync.timer
```

`import-excel` erwartet die Spalten `Ort / Region`, `Trail-Name`, `Gemacht?` (Ja/Nein),
`Datum gemacht`, `Bemerkung` und optional `Mitspieler`; Zeilen unterhalb einer Zeile,
die mit „Manuell ergänzte Trails“ beginnt, werden als manuelle Trails angelegt.

### Eigene Bestellungen von foxtrail.ch übernehmen

Auf foxtrail.ch unter **Account → Deine Bestellungen** stehen alle gebuchten
Trails. Am einfachsten: im selben Browser, angemeldet, die Adresse
`https://foxtrail.ch/wp-json/foxtrail/v1/proxy/account` öffnen und mit Ctrl+S als
`konto.json` speichern. Dann in der App unter ⚙ → **Import** hochladen (nur Administratoren): Die Seite
zeigt einen Probelauf und trägt nach Bestätigung ein. Alternativ die Bestellseite
als „Webseite, vollständig“ speichern oder den Seitentext einfügen (dann ohne
Zeiten und Foto). Dasselbe auf der Kommandozeile:

```bash
foxtrailctl import-bestellungen konto.json              # Probelauf, zeigt nur an
foxtrailctl import-bestellungen konto.json --schreiben  # trägt ein (--ohne-fotos: keine Fotos laden)
```

Aus dem JSON kommen zusätzlich Start- und Zielzeit (daraus die **Spielzeit**,
sortierbare Spalte „Zeit“), Team-Code und Bestellnummer. Das **Schlussfoto** wird
einmalig heruntergeladen, unter `/var/lib/foxtrail-tracker/fotos` abgelegt und auf
der Detailseite gezeigt. Bereits gemachte Trails werden nur um fehlende Angaben
ergänzt, nie überschrieben. Ein von Hand gelöschtes Foto lädt der Import nicht wieder;
auf der Detailseite lässt es sich mit „Schlussfoto von foxtrail.ch laden“ zurückholen.

Zuordnung über den genauen Trail-Namen („Trail Columban“ → Columban, nicht
Columban Mini). Eingetragen werden Datum (Startzeit) und Mitspieler (Erwachsene
plus Kinder, mehrere Teams zusammengezählt). Bereits gemachte Trails, stornierte
Bestellungen und Startzeiten in der Zukunft werden übersprungen, unbekannte Namen
aufgelistet. Mit `-` statt Dateiname liest der Befehl von der Standardeingabe.

## Lokal entwickeln

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python3 scripts/manage.py seed
FOXTRAIL_PASSWORD=geheim123 python3 scripts/manage.py create-user admin --admin
python3 wsgi.py            # http://127.0.0.1:8080  (Debug-Server, DB in data/foxtrail.local.db)
python3 -m pytest          # 19 Tests: Scraper (offline), Abgleich-Regeln, Web-App
```

## Konfiguration

Alles über Umgebungsvariablen (siehe `deploy/foxtrail-tracker.env.example`):

| Variable | Default | Bedeutung |
|---|---|---|
| `SECRET_KEY` | zufällig pro Start | Session-Signatur – in Produktion fest setzen |
| `FOXTRAIL_DB` | `/var/lib/foxtrail-tracker/foxtrail.db` bzw. `data/foxtrail.local.db` | SQLite-Datei |
| `BIND` | `0.0.0.0:8080` | Adresse für gunicorn (nur in der systemd-Unit verwendet); `127.0.0.1:8080` nur, wenn der Reverse-Proxy im selben Container läuft |
| `FORCE_HTTPS` | `0` | `1` = Session-Cookie nur über HTTPS; dann keine Anmeldung mehr über `http://` |
| `FOXTRAIL_LIST_URL` | `https://foxtrail.ch/kategorie/trails/` | Quelle des Abgleichs |
| `SCRAPER_DELAY` | `1.0` | Pause zwischen Seitenabrufen (Sekunden) |
| `LOGIN_MAX_FAILS` / `LOGIN_LOCK_SECONDS` | `5` / `300` | Sperre nach Fehlversuchen |

## Aufbau

```
foxtrail/
  __init__.py   Flask-App: Login, Trail-Liste, Archiv, Bearbeiten, Admin
  config.py     Umgebungsvariablen
  db.py         SQLite-Schema (trails, users, sync_log)
  users.py      Benutzer, Passwort-Hashing (werkzeug)
  scraper.py    foxtrail.ch abrufen und parsen (parse_page ist offline testbar)
  sync.py       Abgleich-Regeln
  trails.py     Lesen/Schreiben der Trail-Liste
  templates/, static/style.css
data/trails_seed.json   Momentaufnahme der Trail-Liste für die Erstbefüllung
scripts/manage.py       Verwaltungs-CLI
deploy/                 install.sh, systemd-Units, foxtrailctl, env-Beispiel
tests/                  pytest
```

Ein Trail ist „archiviert“, wenn `quelle = 'foxtrail' AND im_angebot = 0 AND gemacht = 0`
– das wird nicht gespeichert, sondern abgeleitet. Deshalb kann ein gemachter Trail
nie im Archiv landen, egal was der Abgleich tut.

## Hinweis zum Scraper

Der Abgleich ruft die öffentliche Trail-Übersicht ab (ca. 9 Seiten) und zusätzlich
die nach Schwierigkeit gefilterte Übersicht je Stufe (ca. 8 Seiten) – insgesamt rund
17 Seitenabrufe, eine Sekunde Pause dazwischen, standardmässig einmal pro Woche. Er
nennt sich im User-Agent.
Bitte die Frequenz nicht unnötig erhöhen.

Die Titelbilder für die Kachelansicht (441×294 px, rund 40 KB) lädt der Server erst,
wenn eine Kachel zum ersten Mal angezeigt wird, und legt sie unter `fotos/titel/` ab.
Danach fragt weder er noch der Browser erneut bei foxtrail.ch an – ausser das Bild
ändert sich dort (neue Adresse). Ändert Foxtrail den Aufbau der Seite,
bricht der Abgleich kontrolliert ab („Keine Trails gefunden“) – dann muss
`foxtrail/scraper.py` angepasst werden; `tests/fixture_page.html` zeigt die
erwartete Struktur.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
