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

* Liste aller aktuell angebotenen Trails (Foxtrail und Foxtrail GO) mit Filter
  (alle / offen / gemacht), Typ-Filter und Volltextsuche
* Pro Trail: **gemacht**, **Datum**, **Anzahl Mitspieler**, **Bemerkung**
  (ein Eintrag je Trail, gemeinsam für alle Benutzer)
* Manuell erfasste Trails für früher gemachte Trails, die nicht mehr angeboten werden
* Abgleich mit foxtrail.ch – wöchentlich per systemd-Timer und per Knopfdruck
* Archiv: nicht mehr angebotene, noch nicht gemachte Trails
* Mehrere Benutzer mit Login (gehashte Passwörter, Sperre nach 5 Fehlversuchen),
  Administratoren verwalten Benutzer und lösen den Abgleich aus
* Keine externen Abhängigkeiten im Browser (kein CDN), hell/dunkel automatisch,
  auf dem Handy brauchbar

## Abgleich-Regeln

| Situation | Ergebnis |
|---|---|
| Trail neu auf foxtrail.ch | wird angelegt (offen) |
| Trail weiterhin gelistet | Metadaten (Preis, Dauer, Bewertung, Route) aktualisiert – eigene Einträge bleiben |
| Trail nicht mehr gelistet, **bereits gemacht** | bleibt in der Hauptliste, markiert „nicht mehr im Angebot“ |
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

**Reverse-Proxy / HTTPS:** Läuft die App hinter nginx, Caddy oder Traefik mit TLS,
in `/etc/foxtrail-tracker.env` `BIND=127.0.0.1:8080` und `FORCE_HTTPS=1` setzen,
dann `systemctl restart foxtrail`.

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
journalctl -u foxtrail -u foxtrail-sync  # Logs
systemctl list-timers foxtrail-sync.timer
```

`import-excel` erwartet die Spalten `Ort / Region`, `Trail-Name`, `Gemacht?` (Ja/Nein),
`Datum gemacht`, `Bemerkung` und optional `Mitspieler`; Zeilen unterhalb einer Zeile,
die mit „Manuell ergänzte Trails“ beginnt, werden als manuelle Trails angelegt.

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
| `BIND` | `0.0.0.0:8080` | Adresse für gunicorn (nur in der systemd-Unit verwendet) |
| `FORCE_HTTPS` | `0` | `1` = Session-Cookie nur über HTTPS |
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

Der Abgleich ruft die öffentliche Trail-Übersicht ab (ca. 9 Seiten, eine Sekunde
Pause dazwischen, standardmässig einmal pro Woche) und nennt sich im User-Agent.
Bitte die Frequenz nicht unnötig erhöhen. Ändert Foxtrail den Aufbau der Seite,
bricht der Abgleich kontrolliert ab („Keine Trails gefunden“) – dann muss
`foxtrail/scraper.py` angepasst werden; `tests/fixture_page.html` zeigt die
erwartete Struktur.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
