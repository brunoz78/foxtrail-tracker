# -*- coding: utf-8 -*-
"""
SQLite-Zugriff und Schema.

Tabellen
  trails    - ein Datensatz je Trail (Schluessel: slug = URL-Pfad auf foxtrail.ch,
              bei manuell erfassten Trails "manual-<uuid>")
  users     - Benutzerkonten (Passwort als Hash, werkzeug.security)
  sync_log  - ein Eintrag je Abgleich mit foxtrail.ch

Status eines Trails wird NICHT gespeichert, sondern abgeleitet:
  archiviert  <=>  quelle = 'foxtrail' AND im_angebot = 0 AND gemacht = 0
Alles andere ist "aktiv". Damit kann ein bereits gemachter Trail nie im Archiv
landen, und ein aus dem Archiv heraus als gemacht markierter Trail wandert
automatisch zurueck in die Hauptliste.
"""

import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS trails (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT NOT NULL UNIQUE,
    quelle        TEXT NOT NULL DEFAULT 'foxtrail',   -- 'foxtrail' | 'manual'
    ort           TEXT NOT NULL,
    name          TEXT NOT NULL,
    route         TEXT NOT NULL DEFAULT '',
    typ           TEXT NOT NULL DEFAULT 'foxtrail',   -- 'foxtrail' | 'mini' | 'maxi' | 'go'
    region        TEXT NOT NULL DEFAULT '',
    bewertung     REAL,
    dauer         TEXT NOT NULL DEFAULT '',
    preis         REAL,
    url           TEXT NOT NULL DEFAULT '',
    im_angebot    INTEGER NOT NULL DEFAULT 1,         -- aktuell auf foxtrail.ch gelistet
    first_seen    TEXT NOT NULL,
    last_seen     TEXT,
    neu_seit      TEXT,                              -- Datum, an dem der Abgleich den Trail neu
                                                     -- angelegt hat (NULL: Seed oder manuell)
    schwierigkeit TEXT,                              -- 'einfach' | 'mittel' | 'schwierig', NULL = unbekannt/GO
    -- aus dem Import der eigenen Bestellungen (foxtrail/bestellungen.py):
    start_zeit    TEXT,                              -- tatsaechlicher Start 'JJJJ-MM-TT HH:MM'
    ziel_zeit     TEXT,                              -- Ankunft im Ziel; Spielzeit wird daraus abgeleitet
    team_code     TEXT,
    bestellung    TEXT,                              -- Bestellnummer bei foxtrail.ch
    foto_url      TEXT,                              -- Quelle des Schlussfotos
    foto          TEXT,                              -- Dateiname unter config.foto_dir();
                                                     -- '' = von Hand geloescht (Import laedt es nicht neu)
    bild_url      TEXT,                              -- Titelbild auf foxtrail.ch (Kachelansicht)
    gemacht       INTEGER NOT NULL DEFAULT 0,
    gemacht_datum TEXT,
    mitspieler    INTEGER,
    bemerkung     TEXT NOT NULL DEFAULT '',
    erfasst_von   TEXT,
    erfasst_am    TEXT
);
CREATE INDEX IF NOT EXISTS ix_trails_ort ON trails(ort, name);

CREATE TABLE IF NOT EXISTS users (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username  TEXT NOT NULL UNIQUE,
    pw_hash   TEXT NOT NULL,
    is_admin  INTEGER NOT NULL DEFAULT 0,
    active    INTEGER NOT NULL DEFAULT 1,
    created   TEXT NOT NULL,
    last_login TEXT,
    spalten   TEXT,                  -- sichtbare Spalten der Liste, 'route,typ,...'; NULL = Standard
    nur_lesen INTEGER NOT NULL DEFAULT 0,  -- 1 = darf nichts aendern (nie zusammen mit is_admin)
    sprache   TEXT,                  -- de | fr | it | en; NULL = automatisch (Browser)
    anzeigename   TEXT,
    pw_wechsel    INTEGER NOT NULL DEFAULT 0,  -- Passwort beim naechsten Login aendern
    pw_fest       INTEGER NOT NULL DEFAULT 0,  -- Passwort darf nicht selbst geaendert werden
    twofa_pflicht INTEGER NOT NULL DEFAULT 0,  -- Zweitfaktor muss eingerichtet sein
    totp_secret   TEXT,                        -- Authenticator-App (base32), NULL = keine
    passkeys      TEXT NOT NULL DEFAULT '[]'   -- WebAuthn: [{id, public_key, sign_count, name, created}]
);

-- Anmelde-Protokoll (foxtrail/authlog.py)
CREATE TABLE IF NOT EXISTS authlog (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    benutzer TEXT NOT NULL DEFAULT '',
    ereignis TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '',
    ip       TEXT NOT NULL DEFAULT '',
    ua       TEXT NOT NULL DEFAULT ''
);

-- einfache Einstellungen der App (z. B. 'abgleich' = woechentlich | monatlich | aus)
CREATE TABLE IF NOT EXISTS einstellungen (
    schluessel TEXT PRIMARY KEY,
    wert       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                   TEXT NOT NULL,
    ausloeser            TEXT NOT NULL,
    ok                   INTEGER NOT NULL,
    gefunden             INTEGER NOT NULL DEFAULT 0,
    neu                  INTEGER NOT NULL DEFAULT 0,
    aktualisiert         INTEGER NOT NULL DEFAULT 0,
    reaktiviert          INTEGER NOT NULL DEFAULT 0,
    archiviert           INTEGER NOT NULL DEFAULT 0,
    nicht_mehr_im_angebot INTEGER NOT NULL DEFAULT 0,
    meldung              TEXT NOT NULL DEFAULT ''
);
"""

ARCHIV_COND = "(quelle = 'foxtrail' AND im_angebot = 0 AND gemacht = 0)"


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect(path=None):
    conn = sqlite3.connect(path or config.db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn):
    """Kleine, idempotente Migrationen fuer bestehende Datenbanken."""
    u_spalten = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "spalten" not in u_spalten:
        # 2026-09: Spaltenauswahl der Liste je Benutzer
        conn.execute("ALTER TABLE users ADD COLUMN spalten TEXT")
    if "nur_lesen" not in u_spalten:
        # 2026-09: Rolle "Nur lesen"
        conn.execute("ALTER TABLE users ADD COLUMN nur_lesen INTEGER NOT NULL DEFAULT 0")
    if "sprache" not in u_spalten:
        # 2026-09: Oberflaeche auf Deutsch, Franzoesisch, Italienisch, Englisch
        conn.execute("ALTER TABLE users ADD COLUMN sprache TEXT")
    # 2026-09: erweiterte Benutzerverwaltung (Passwort-Optionen, 2FA)
    for spalte, typ in (('anzeigename', 'TEXT'), ('pw_wechsel', 'INTEGER NOT NULL DEFAULT 0'), ('pw_fest', 'INTEGER NOT NULL DEFAULT 0'), ('twofa_pflicht', 'INTEGER NOT NULL DEFAULT 0'), ('totp_secret', 'TEXT'), ('passkeys', "TEXT NOT NULL DEFAULT '[]'")):
        if spalte not in u_spalten:
            conn.execute(f"ALTER TABLE users ADD COLUMN {spalte} {typ}")
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(trails)")}
    if "neu_seit" not in spalten:
        # 2026-09: "Neu ab MM/JJ" fuer Trails, die ein Abgleich neu angelegt hat. Rueckwirkend
        # laesst sich das am first_seen erkennen: der Seed schreibt fuer alle Trails denselben
        # Zeitstempel (den fruehesten), alles Spaetere kam ueber einen Abgleich.
        conn.execute("ALTER TABLE trails ADD COLUMN neu_seit TEXT")
        conn.execute("UPDATE trails SET neu_seit = substr(first_seen, 1, 10) WHERE quelle = 'foxtrail' "
                     "AND first_seen > (SELECT MIN(first_seen) FROM trails WHERE quelle = 'foxtrail')")
    if "schwierigkeit" not in spalten:
        # 2026-09: Schwierigkeitsgrad; wird vom naechsten Abgleich gefuellt (foxtrailctl sync).
        conn.execute("ALTER TABLE trails ADD COLUMN schwierigkeit TEXT")
    for spalte in ("start_zeit", "ziel_zeit", "team_code", "bestellung", "foto_url", "foto"):
        if spalte not in spalten:
            # 2026-09: Import der Bestellungen (Zeiten, Team-Code, Bestellnummer, Schlussfoto)
            conn.execute(f"ALTER TABLE trails ADD COLUMN {spalte} TEXT")
    if "bild_url" not in spalten:
        # 2026-09: Titelbild fuer die Kachelansicht; Seed und Abgleich fuellen es.
        conn.execute("ALTER TABLE trails ADD COLUMN bild_url TEXT")
    # 2026-09: Mini/Maxi als eigener Typ (vorher alles 'foxtrail'). Nur Website-Trails -
    # bei manuell erfassten entscheidet der Benutzer selbst. LIKE ist in SQLite fuer
    # ASCII case-insensitive, deckt also "Maxi"/"MAXI" ab (vgl. trails.typ_aus_name).
    conn.execute("UPDATE trails SET typ = 'maxi' WHERE quelle = 'foxtrail' AND typ = 'foxtrail' "
                 "AND name LIKE '% Maxi'")
    conn.execute("UPDATE trails SET typ = 'mini' WHERE quelle = 'foxtrail' AND typ = 'foxtrail' "
                 "AND name LIKE '% Mini'")


@contextmanager
def session(path=None):
    """with session() as conn: ...  -> commit bei Erfolg, rollback bei Fehler."""
    conn = connect(path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
