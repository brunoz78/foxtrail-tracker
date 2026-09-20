# -*- coding: utf-8 -*-
"""
Sicherung: alles in eine ZIP-Datei - und bei einer Neuinstallation wieder hinein.

Inhalt von foxtrail-sicherung-<datum>.zip
  meta.json     Zeitpunkt, Version, Anzahl Trails/Benutzer/Fotos
  foxtrail.db   die ganze SQLite-Datenbank: Trails samt eigenen Eintraegen, Benutzer,
                Einstellungen, Abgleich- und Anmelde-Protokoll
  fotos/*.jpg   die eigenen Schlussfotos

Nicht dabei sind Vorschaubilder (fotos/klein/) und Titelbilder von foxtrail.ch (fotos/titel/) -
die legt die App bei Bedarf neu an - sowie der SECRET_KEY (der gehoert zur Installation, nicht
zu den Daten).

Achtung: Die Datei enthaelt alle Daten, auch Passwort-Hashes und Zweitfaktor-Schluessel. Sie
gehoert an einen sicheren Ort.

Einlesen ersetzt Datenbank und Fotos vollstaendig. Die bisherige Datenbank bleibt als
<name>.alt-<zeitstempel> daneben liegen, damit ein Fehlgriff nicht alles kostet.
"""

import datetime
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile

from . import config, db, version

DB_NAME = "foxtrail.db"
META_NAME = "meta.json"
FOTO_ORDNER = "fotos/"
CACHE_ORDNER = ("klein", "titel")                  # werden neu erzeugt, nicht gesichert
_FOTO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}\.jpg$")


class SicherungFehler(Exception):
    pass


# ---- Lesen ------------------------------------------------------------------------ #
def _fotos(foto_dir):
    """Eigene Fotos (nur die Dateien direkt im Ordner, keine Unterordner)."""
    try:
        namen = os.listdir(foto_dir)
    except OSError:
        return []
    return sorted(n for n in namen if _FOTO_RE.match(n)
                  and os.path.isfile(os.path.join(foto_dir, n)))


def dateiname(jetzt=None):
    tag = (jetzt or datetime.date.today()).isoformat()
    return f"foxtrail-sicherung-{tag}.zip"


def stand(conn, foto_dir=None, db_path=None):
    """Was aktuell in einer Sicherung landen wuerde (fuer die Anzeige)."""
    foto_dir = foto_dir or config.foto_dir()
    db_path = db_path or config.db_path()
    groesse = 0
    for endung in ("", "-wal"):
        try:
            groesse += os.path.getsize(db_path + endung)
        except OSError:
            pass
    return {"trails": conn.execute("SELECT COUNT(*) FROM trails").fetchone()[0],
            "gemacht": conn.execute("SELECT COUNT(*) FROM trails WHERE gemacht = 1").fetchone()[0],
            "benutzer": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "fotos": len(_fotos(foto_dir)), "db_bytes": groesse}


# ---- Schreiben -------------------------------------------------------------------- #
def _db_kopieren(quelle, ziel):
    """Konsistente Kopie ueber die backup-API - auch wenn gerade jemand schreibt (WAL)."""
    src = sqlite3.connect(quelle, timeout=10)
    dst = sqlite3.connect(ziel)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def erstellen(ziel_pfad, db_path=None, foto_dir=None):
    """Sicherung schreiben; gibt meta.json als dict zurueck."""
    db_path = db_path or config.db_path()
    foto_dir = foto_dir or config.foto_dir()
    fotos = _fotos(foto_dir)
    with tempfile.TemporaryDirectory() as tmp:
        kopie = os.path.join(tmp, DB_NAME)
        _db_kopieren(db_path, kopie)
        conn = sqlite3.connect(kopie)
        try:
            meta = {"app": "foxtrail-tracker", "version": version.VERSION,
                    "erstellt": db.now(),
                    "trails": conn.execute("SELECT COUNT(*) FROM trails").fetchone()[0],
                    "benutzer": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                    "fotos": len(fotos)}
        finally:
            conn.close()
        with zipfile.ZipFile(ziel_pfad, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(META_NAME, json.dumps(meta, ensure_ascii=False, indent=1))
            z.write(kopie, DB_NAME)
            for name in fotos:
                z.write(os.path.join(foto_dir, name), FOTO_ORDNER + name)   # JPEG bleibt wie es ist
    return meta


# ---- Einlesen --------------------------------------------------------------------- #
def _db_pruefen(pfad):
    """Sieht die Datei wie unsere Datenbank aus? Bringt sie gleich auf den neuesten Stand."""
    try:
        conn = sqlite3.connect(pfad)
    except sqlite3.Error as ex:
        raise SicherungFehler(f"Die Datenbank in der Sicherung ist unbrauchbar: {ex}") from None
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SicherungFehler("Die Datenbank in der Sicherung ist beschädigt.")
        tabellen = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"trails", "users"} <= tabellen:
            raise SicherungFehler("Die Datei enthält keine Foxtrail-Datenbank.")
        db.init_db(conn)                     # aeltere Sicherung: fehlende Spalten nachziehen
        return {"trails": conn.execute("SELECT COUNT(*) FROM trails").fetchone()[0],
                "benutzer": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]}
    except sqlite3.DatabaseError as ex:
        raise SicherungFehler(f"Die Datenbank in der Sicherung ist unbrauchbar: {ex}") from None
    finally:
        conn.close()


def _db_tauschen(neu, db_path):
    """Alte Datenbank zur Seite legen (samt WAL) und die neue an ihren Platz stellen."""
    alt = ""
    if os.path.exists(db_path):
        alt = f"{db_path}.alt-{time.strftime('%Y%m%d-%H%M%S')}"
        os.replace(db_path, alt)
    for endung in ("-wal", "-shm"):                 # gehoeren zur alten Datei, sonst Mischmasch
        try:
            os.remove(db_path + endung)
        except OSError:
            pass
    shutil.move(neu, db_path)                       # move: der Temp-Ordner liegt oft woanders
    return alt


def _fotos_ersetzen(z, foto_dir):
    os.makedirs(foto_dir, exist_ok=True)
    for name in _fotos(foto_dir):
        try:
            os.remove(os.path.join(foto_dir, name))
        except OSError:
            pass
    for ordner in CACHE_ORDNER:                     # Vorschau und Titelbilder neu aufbauen lassen
        shutil.rmtree(os.path.join(foto_dir, ordner), ignore_errors=True)
    anzahl = 0
    for eintrag in z.namelist():
        if not eintrag.startswith(FOTO_ORDNER):
            continue
        name = os.path.basename(eintrag)            # nie Pfade aus der ZIP-Datei uebernehmen
        if not _FOTO_RE.match(name):
            continue
        with z.open(eintrag) as quelle, open(os.path.join(foto_dir, name), "wb") as ziel:
            shutil.copyfileobj(quelle, ziel)
        anzahl += 1
    return anzahl


def einlesen(zip_pfad, db_path=None, foto_dir=None):
    """Datenbank und Fotos aus einer Sicherung wiederherstellen (ersetzt alles Bisherige).

    Vorher darf keine Verbindung zur Datenbank mehr offen sein.
    """
    db_path = db_path or config.db_path()
    foto_dir = foto_dir or config.foto_dir()
    try:
        z = zipfile.ZipFile(zip_pfad)
    except (zipfile.BadZipFile, OSError):
        raise SicherungFehler("Das ist keine lesbare ZIP-Datei.") from None
    with z:
        if DB_NAME not in z.namelist():
            raise SicherungFehler("Das ist keine Sicherung des Foxtrail-Trackers "
                                  "(foxtrail.db fehlt).")
        with tempfile.TemporaryDirectory() as tmp:
            neu = os.path.join(tmp, DB_NAME)
            with z.open(DB_NAME) as quelle, open(neu, "wb") as ziel:
                shutil.copyfileobj(quelle, ziel)
            zahlen = _db_pruefen(neu)
            os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
            alt = _db_tauschen(neu, db_path)
        zahlen["fotos"] = _fotos_ersetzen(z, foto_dir)
    zahlen["alt"] = alt
    return zahlen
