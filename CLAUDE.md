# CLAUDE.md – Foxtrail-Tracker

Kontext für Claude Code. Auf Deutsch antworten, kurz und präzise. Komplette,
lauffähige Dateien liefern statt Teil-Diffs. Kommandozeile vor GUI.

## Was das ist

Selbst gehostete Flask-App (SQLite), mit der wir festhalten, welche Schweizer
Foxtrails (foxtrail.ch) wir gemacht haben – pro Trail: gemacht, Datum, Anzahl
Mitspieler, Bemerkung. Läuft in einem Debian-LXC auf Proxmox, mehrere Benutzer mit
Login. Wird auf GitHub (brunoz78/foxtrail-tracker) veröffentlicht, MIT-Lizenz –
Code muss generisch bleiben (keine persönlichen Daten, alles über Env-Variablen).

Das Projekt ist privat und nicht mit der Foxtrail AG verbunden; die Trail-Daten
gehören ihr. Disclaimer im README und im Footer nicht entfernen.

## Getroffene Entscheidungen (nicht ohne Rücksprache ändern)

- **Login schlank**, nach dem Muster des Lohn-Dashboards: Flask-Session-Cookie
  (HttpOnly, SameSite=Lax, kein permanentes Cookie), Passwörter gehasht mit
  `werkzeug.security`, Sperre nach 5 Fehlversuchen für 5 Minuten (In-Memory).
  Bewusst **kein** 2FA, **kein** Rollen-/Rechte-System, **kein** Audit-Log – nur
  das Flag `is_admin` (Benutzerverwaltung + Abgleich auslösen).
- **Eine gemeinsame Liste** für alle Benutzer, **ein Eintrag pro Trail** (kein
  Mehrfach-Tracking). `erfasst_von` hält fest, wer zuletzt gespeichert hat.
- **Mitspieler** = einfaches Zahlenfeld, keine Namen.
- **Typ** ist `foxtrail | mini | maxi | go` und wird aus dem Namen abgeleitet
  (`trails.typ_aus_name`: Wort „Mini“/„Maxi“ im Namen, GO vom Badge „Digitale
  Schnitzeljagd“). foxtrail.ch zeigt MINI/MAXI nur als Badge im Titelbild, im HTML gibt
  es kein Element dafür. `db._migrate` klassifiziert Altbestand nach (nur `quelle='foxtrail'`).
- **Sortieren und Filtern** serverseitig über GET-Parameter (`sort`, `dir`, mehrfach
  `typ`, `region`, `dauer`), Sortierung in Python (`trails.SORTS`, leere Werte immer am
  Ende). `dauer` bleibt Text in der DB und wird zur Laufzeit geparst (`trails.parse_dauer`),
  keine zusätzlichen Spalten. Anzeige „1.5–2.5 h“ über den Jinja-Filter `dauer`.
  Die Haken-Filter sitzen in den Spaltenköpfen (`_macros.html`, `<details>`, geht ohne JS;
  ein paar Zeilen Vanilla-JS wenden die Auswahl beim Schliessen des Menüs an).
- **Schwierigkeit** (`schwierigkeit`: `einfach | mittel | schwierig | NULL`) steht nicht in den
  Karten der Übersicht. Statt 97 Detailseiten liest der Scraper die Übersicht dreimal
  gefiltert (`?filters=difficulty[<id>]`, IDs aus dem Filter-Widget auf Seite 1, ~8 Seiten).
  Steht ein Trail in zwei Stufen (Varianten), zählt die höhere. `None` vom Scraper heisst
  „nicht ermittelt“: der Abgleich überschreibt einen bekannten Wert nie mit NULL (COALESCE).
  GO-Trails haben keine Stufe. Mit Bruno am 2026-09-17 besprochen.
- **Import-Daten** (`start_zeit`, `ziel_zeit`, `team_code`, `bestellung`, `foto_url`, `foto`)
  kommen nur aus dem Bestellungs-Import und werden vom Abgleich nie angefasst. Spielzeit
  wird aus Start/Ziel abgeleitet (`trails.spielzeit_min`), nicht gespeichert. Schlussfotos
  liegen als `<id>.jpg` in `config.foto_dir()` (LXC: `/var/lib/foxtrail-tracker/fotos`,
  lokal `data/fotos`, gitignored) und werden ueber `/foto/<id>` nur angemeldet ausgeliefert.
  Kein Weg B (Tracker meldet sich selbst bei foxtrail.ch an) ohne neue Absprache.
- **Fotos** (`foxtrail/fotos.py`): Upload auf der Detailseite (`POST /trail/<id>/foto`,
  aktion `hochladen | loeschen | foxtrail`). Pillow dreht nach EXIF, verkleinert auf 2560 px
  und speichert als JPEG `<id>-<zeit>.jpg` (entfernt EXIF/GPS; neuer Name = neue URL, daher
  langer Browser-Cache ok). `foto = ''` heisst „von Hand geloescht“: der Import laedt es
  dann nicht wieder (`bestellungen.anwenden` prueft `is None`). Vorschau 640 px unter
  `fotos/klein/`, erzeugt beim ersten Abruf von `/foto/<id>?g=klein`.
- **Kachelansicht** (`?ansicht=kacheln`, gemerkt in der Session): Bild = eigenes Foto,
  sonst Titelbild von foxtrail.ch (`bild_url`, vom Scraper aus `data-lazy-src`/`src`,
  im Seed hinterlegt). Titelbilder laedt der Server einmalig bei Bedarf nach
  `fotos/titel/<id>-<hash>.jpg` und liefert sie ueber `/titelbild/<id>` aus – der Browser
  fragt nie direkt bei foxtrail.ch an. Nur URLs unter `https://foxtrail.ch/`. Mit Bruno
  am 2026-09-18 so vereinbart und ausdrücklich bestätigt („Titelbilder sollen geladen werden“).
  Gemachte Kacheln: Ring und hellgrüner Textbereich im MINI-Grün (`--gemacht*` in style.css).
- **Abgleich mit foxtrail.ch** automatisch (systemd-Timer, wöchentlich) **und**
  manuell (Button im Admin-Bereich).
- **Es wird nie ein Trail gelöscht.** Regeln in `foxtrail/sync.py`:
  - neu auf der Website → anlegen (offen, `neu_seit` = Datum; Liste zeigt dauerhaft
    „Neu ab MM/JJ“, Filter `f=neu` listet alle mit `neu_seit`, Standard-Sortierung dort
    neueste zuerst – bewusst keine Frist, Bruno will alle Zugänge sehen). Manuelle Trails haben `neu_seit`
    NULL. Der Seed auch, ausser bei Trails, die laut foxtrail.ch/thema/neuigkeiten/ kürzlich
    eröffnet wurden (Datum des Blog-Beitrags, von Hand gepflegt, kein Scraping – die
    Seite ist ein Blog ohne Trail-Links, das NEW-Badge ist wie MINI/MAXI nur im Bild).
    `seed_from_file` trägt `neu_seit` aus dem Seed bei bestehenden Trails nach, wenn dort
    NULL steht; `dev-update.sh` und das community-script rufen `seed` nach dem Update auf.
  - weiterhin gelistet → Metadaten aktualisieren, eigene Einträge unangetastet
  - nicht mehr gelistet + gemacht → bleibt in der Hauptliste, „nicht mehr im Angebot
    seit MM/JJ“ (Monat aus `last_seen`, keine eigene Spalte)
  - nicht mehr gelistet + offen → **Archiv**
  - archiviert und als gemacht markiert → zurück in die Hauptliste
  - taucht wieder auf → reaktiviert
  - manuell erfasste Trails (`quelle='manual'`) fasst der Abgleich nie an
  - Sicherung: < 50 % der bekannten Trails gefunden → Abbruch ohne Änderung
- **Archiv-Status wird nicht gespeichert, sondern abgeleitet**
  (`db.ARCHIV_COND`: `quelle='foxtrail' AND im_angebot=0 AND gemacht=0`).
  Nicht durch eine Status-Spalte ersetzen – die Ableitung garantiert, dass ein
  gemachter Trail nie im Archiv landet.
- **Schlüssel** für den Abgleich ist der URL-Slug (`wallis/allalin-maxi`), nie der Name.
- Kein CDN, kein JavaScript-Framework: Templates + eine CSS-Datei, hell/dunkel per
  `prefers-color-scheme`.
- **Kopfzeile:** Trails · Statistik · „Mehr ▾“ (Archiv, Trail manuell erfassen) · „⚙ ▾“
  (Import, Abgleich + Benutzer nur Admin, Passwort ändern, Abmelden). Aufklappmenüs sind
  `<details class="dd">` in `base.html`, JS schliesst sie bei Klick daneben/Escape. Auf dem
  Handy klappen sie im ☰-Menü nach unten auf. Wunsch von Bruno (2026-09-18): Kopfzeile schlank halten.
- **Smartphone** (unter 700 px, Block am Ende von `style.css`): Menü hinter ☰ (nur mit JS
  eingeklappt, Klasse `js` am `<html>`). Liste und Archiv (`table.karten`) werden per CSS zu
  Karten: `thead` wird zur umbrechenden Sortier-/Filterleiste, jede Zeile ein Flex-Block,
  Anordnung über die Klassen `m-*` (Zellen) und `k-*` (Köpfe). Kein zweites Markup - sonst
  gäbe es die Filter-Checkboxen doppelt im selben Formular. Neue Spalten brauchen eine
  `m-`/`k-`-Klasse und eine Regel im Handy-Block.

## Aufbau

```
foxtrail/__init__.py   create_app(): Routen, Login, Filter (chf, datum)
foxtrail/config.py     Env-Variablen (FOXTRAIL_DB, SECRET_KEY, FORCE_HTTPS, ...)
foxtrail/db.py         Schema trails / users / sync_log, ARCHIV_COND
foxtrail/users.py      Benutzer, Hashing, UserError
foxtrail/scraper.py    parse_page(html) rein + offline testbar, fetch_all() mit Netz
foxtrail/sync.py       apply(conn, scraped, ausloeser) – die Regeln oben
foxtrail/trails.py     Lesen/Schreiben, manuelle Trails, seed_from_file (insert-only)
foxtrail/bestellungen.py  Import "Deine Bestellungen" (foxtrail.ch-Konto): parse (JSON von
                       /wp-json/foxtrail/v1/proxy/account, HTML oder Seitentext), zuordnen
                       (Plan: setzen | ergaenzen | uebersprungen | unbekannt), anwenden (inkl.
                       Schlussfoto-Download nach config.foto_dir()). Zuordnung nur ueber
                       exakten Namen. Die JSON-Adresse braucht das Session-Cookie des Browsers;
                       der Tracker ruft sie nie selbst ab - Upload auf der Seite /import.
foxtrail/trails.py:statistik()  Kennzahlen fuer /statistik (Hauptliste ohne Archiv; Jahre mit
                       Luecken als 0, Spielzeit nur aus importierten Zeiten)
foxtrail/fotos.py      Fotos pruefen/verkleinern (Pillow), Vorschaubilder, Titelbild-Cache
foxtrail/templates/    base, login, index (Liste + Kacheln), statistik, archiv, trail_form, trail_new, profil,
                       benutzer, sync, fehler, import (Upload + Probelauf + Bestaetigen),
                       _macros (Typ-Badge, Sortier-Link, Spaltenfilter)
data/trails_seed.json  Momentaufnahme (97 Trails, Stand 2026-09-17) für die Erstbefüllung
scripts/manage.py      CLI: init-db, seed, sync, create-user, set-password, list-users,
                       stats, export-json, import-excel, import-bestellungen (Probelauf
                       ohne --schreiben)
deploy/                install.sh (Debian-LXC), foxtrail.service, foxtrail-sync.service,
                       foxtrail-sync.timer, foxtrailctl, env-Beispiel,
                       dev-update.sh (Branch-Stand ohne Release in den Test-LXC)
proxmox/               community-scripts: ct/, install/, json/ + README.md (Quelle;
                       Fork brunoz78/ProxmoxVED ist nur die Auslieferung)
docs/logo.svg          Logo fuer die JSON-Metadaten
tests/                 pytest: test_scraper (Fixture-HTML), test_sync, test_app (Testclient),
                       test_bestellungen (anonymisiertes Muster der Kontoseite)
```

## Befehle

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest
python3 -m pytest -q                      # muss grün sein, bevor committet wird
python3 scripts/manage.py seed
FOXTRAIL_PASSWORD=geheim123 python3 scripts/manage.py create-user admin --admin
python3 wsgi.py                           # Debug-Server auf http://127.0.0.1:8080
```

Im LXC: `bash deploy/install.sh` (idempotent), danach `foxtrailctl <befehl>`.
Aktualisieren im LXC: `git pull && bash deploy/install.sh`.
Alternativ per community-script vom Proxmox-Host aus (README), Update dort mit
`update` im Container.

## Arbeitsregeln

- Vor jedem Commit `python3 -m pytest -q` ausführen. Neue Sync-Regeln oder
  Scraper-Änderungen brauchen einen Test (`tests/test_sync.py`,
  `tests/fixture_page.html` zeigt die erwartete HTML-Struktur von foxtrail.ch).
- Schema-Änderungen: `db.SCHEMA` ist `CREATE TABLE IF NOT EXISTS`; neue Spalten
  brauchen eine kleine Migration in `db.init_db` (`ALTER TABLE ... ADD COLUMN`,
  vorher per `PRAGMA table_info` prüfen), damit bestehende Datenbanken im LXC
  weiterlaufen.
- Der Scraper darf foxtrail.ch nicht öfter als nötig abrufen (Standard: wöchentlich,
  1 s Pause zwischen Seiten, sprechender User-Agent). Keine weiteren Seiten als die
  Kategorie-Übersicht und deren Schwierigkeits-Filteransichten abgrasen, ohne dass das
  besprochen wurde (insbesondere keine Detailseiten).
- **Umlaute:** Alles, was im Browser erscheint (Templates, Flash-Meldungen, Fehlertexte,
  Titel), schreibt ä/ö/ü – nie ae/oe/ue (Wunsch von Bruno, 2026-09-18). ASCII bleibt bei
  internen Schlüsseln (`aktion='ergaenzen'`, `value="loeschen"`, Spaltennamen, URLs), in
  Code-Kommentaren und in CLI-/Log-Ausgaben. Tests prüfen die sichtbaren Texte mit Umlaut.
- Commit-Messages auf Deutsch, Betreffzeile < 70 Zeichen, Begründung im Body.
- Keine Secrets, Datenbanken (`data/*.db`) oder `.env` committen (`.gitignore` beachten).
- `proxmox/` ist die Quelle der Proxmox-Scripts. Nach Aenderungen die drei Dateien
  in den Fork brunoz78/ProxmoxVED (`ct/`, `install/`, `json/`) kopieren und pushen.
  Das Script installiert immer das neueste GitHub-Release: App-Aenderungen wirken im
  LXC erst nach einem neuen Tag + Release (`gh release create vX.Y.Z --generate-notes`).
  Dort laeuft alles als root ohne Dienstbenutzer (community-scripts-Konvention);
  Kommentare und Ausgaben in `proxmox/` bleiben englisch. Das Repo muss dafuer
  oeffentlich sein.
- **Kein Release ohne Freigabe.** Ablauf: Aenderung auf `main` pushen, Bruno testet im
  LXC mit `deploy/dev-update.sh` (holt den Branch-Stand ohne Release), erst nach seinem
  OK Tag + Release erstellen.

## Offene Ideen (nicht begonnen)

- Excel-Export der Liste (Gegenstück zu `import-excel`)
- Karte nach Region (Filter, Sortierung und Gruppen-Zeilen nach Region gibt es seit 2026-09)
