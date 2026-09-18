# -*- coding: utf-8 -*-
"""
Woechentlicher Abgleich ohne systemd (Docker-Image).

Im LXC erledigt das deploy/foxtrail-sync.timer. Im Container gibt es kein systemd; dort
startet docker/entrypoint.sh `manage.py zeitplan` als Hintergrundprozess, der sich gleich
verhaelt wie der Timer:
  * jeden Montag um 04:30 (Ortszeit, TZ), bis zu 30 Min. zufaellig spaeter
  * ein verpasster Termin (Container war aus) wird kurz nach dem Start nachgeholt;
    bei einer Neuinstallation (noch nie automatisch abgeglichen) gibt es nichts nachzuholen,
    wie beim systemd-Timer mit Persistent=true

Der Prozess schreibt seinen naechsten Termin und einen Herzschlag nach
<Datenordner>/zeitplan.json; die Seite Abgleich liest daraus den Status (status()).
"""

import datetime
import json
import os
import random
import sys
import time

from . import db, sync

WOCHENTAG = 0                # Montag
STUNDE, MINUTE = 4, 30
MAX_VERZOEGERUNG = 30 * 60   # Sekunden, wie RandomizedDelaySec=30min
START_PAUSE = 60             # nachgeholter Lauf erst, wenn der Webserver steht
HERZSCHLAG = 15 * 60         # so oft wird zeitplan.json aufgefrischt
VERALTET = 2 * HERZSCHLAG    # aelter -> Prozess laeuft nicht mehr

PLAN_TEXT = "jeden Montag um 04:30"


def datei(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "zeitplan.json")


def letzter_termin(jetzt):
    """Juengster regulaere Termin <= jetzt (ohne Zufallsverzoegerung)."""
    t = jetzt.replace(hour=STUNDE, minute=MINUTE, second=0, microsecond=0)
    t -= datetime.timedelta(days=(t.weekday() - WOCHENTAG) % 7)
    if t > jetzt:
        t -= datetime.timedelta(days=7)
    return t


def naechster_termin(jetzt):
    return letzter_termin(jetzt) + datetime.timedelta(days=7)


def faellig(letzter_lauf, jetzt):
    """letzter_lauf = ts des letzten Timer-Laufs aus sync_log ('YYYY-MM-DD HH:MM:SS') oder None.
    True, wenn seit dem letzten regulaeren Termin noch kein Lauf stattfand. None (Neuinstallation)
    zaehlt nicht als verpasst: der erste Lauf ist der naechste regulaere Termin."""
    return bool(letzter_lauf) and letzter_lauf < letzter_termin(jetzt).strftime("%Y-%m-%d %H:%M:%S")


def _schreiben(pfad, naechster, jetzt, nachholen=False):
    """naechster = tatsaechlicher Zeitpunkt (mit Zufallsverzoegerung). Angezeigt wird wie beim
    systemd-Timer der regulaere Termin (04:30) plus der Hinweis auf die Verzoegerung."""
    termin = naechster if nachholen else letzter_termin(naechster)
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"naechster": naechster.strftime("%Y-%m-%d %H:%M:%S"),
                   "termin": termin.strftime("%Y-%m-%d %H:%M:%S"),
                   "herzschlag": jetzt.strftime("%Y-%m-%d %H:%M:%S"), "nachholen": nachholen}, fh)
    os.replace(tmp, pfad)


def status(pfad, jetzt=None):
    """Status fuer die Seite Abgleich im selben Format wie sync.timer_status();
    None, wenn es keinen Zeitplan-Prozess gibt (keine Datei)."""
    try:
        with open(pfad, encoding="utf-8") as fh:
            d = json.load(fh)
        naechster = datetime.datetime.strptime(d.get("termin") or d["naechster"], "%Y-%m-%d %H:%M:%S")
        herzschlag = datetime.datetime.strptime(d["herzschlag"], "%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    jetzt = jetzt or datetime.datetime.now()
    return {
        "aktiv": (jetzt - herzschlag).total_seconds() <= VERALTET,
        "plan": PLAN_TEXT,
        "naechster": sync.zeitpunkt_text(naechster)
                     + (" (verpasster Termin wird nachgeholt)" if d.get("nachholen") else ""),
        "letzter": "",
        "verzoegerung": "" if d.get("nachholen") else f"{MAX_VERZOEGERUNG // 60} Min.",
        "docker": True,
    }


def _log(text):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "zeitplan:", text, file=sys.stderr, flush=True)


def _letzter_lauf(db_path):
    with db.session(db_path) as conn:
        r = conn.execute("SELECT ts FROM sync_log WHERE ausloeser = 'timer' ORDER BY id DESC LIMIT 1").fetchone()
    return r["ts"] if r else None


def _abgleichen(db_path):
    try:
        with db.session(db_path) as conn:
            res = sync.run(conn, ausloeser="timer")
        _log(f"Abgleich {'ok' if res['ok'] else 'FEHLER'}: {res['gefunden']} gefunden, {res['neu']} neu"
             + (f" - {res['meldung']}" if res.get("meldung") else ""))
    except Exception as ex:                      # der Prozess soll weiterlaufen
        _log(f"Abgleich abgebrochen: {ex!r}")


def laufen(db_path):
    """Endlosschleife (manage.py zeitplan)."""
    pfad = datei(db_path)
    jetzt = datetime.datetime.now()
    nachholen = faellig(_letzter_lauf(db_path), jetzt)
    if nachholen:
        ziel = jetzt + datetime.timedelta(seconds=START_PAUSE)
        _log("Termin verpasst, Abgleich wird nachgeholt")
    else:
        ziel = naechster_termin(jetzt) + datetime.timedelta(seconds=random.randint(0, MAX_VERZOEGERUNG))
    while True:
        jetzt = datetime.datetime.now()
        if jetzt >= ziel:
            _abgleichen(db_path)
            nachholen = False
            jetzt = datetime.datetime.now()
            ziel = naechster_termin(jetzt) + datetime.timedelta(seconds=random.randint(0, MAX_VERZOEGERUNG))
        _schreiben(pfad, ziel, jetzt, nachholen)
        time.sleep(max(1, min(HERZSCHLAG, (ziel - jetzt).total_seconds())))
