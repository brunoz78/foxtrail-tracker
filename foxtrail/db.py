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
    last_login TEXT
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
