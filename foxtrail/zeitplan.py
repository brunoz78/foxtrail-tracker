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

Zyklus (Einstellung auf der Seite Abgleich, Tabelle einstellungen): woechentlich | monatlich | aus.
Timer bzw. Zeitplan-Prozess feuern immer montags; termin_faellig() entscheidet, ob wirklich
abgeglichen wird. Monatlich = der erste erfolgreiche Timer-Lauf im Kalendermonat (normalerweise
der erste Montag), die weiteren Montage des Monats fallen aus. So braucht es keinen haeufigeren
Timer und bestehende Installationen muessen nichts umstellen.
"""

import datetime
import json
import os
import random
import sys
import time

from . import db, sync
from .i18n import tr

WOCHENTAG = 0                # Montag
STUNDE, MINUTE = 4, 30
MAX_VERZOEGERUNG = 30 * 60   # Sekunden, wie RandomizedDelaySec=30min
START_PAUSE = 60             # nachgeholter Lauf erst, wenn der Webserver steht
HERZSCHLAG = 15 * 60         # so oft wird zeitplan.json aufgefrischt
VERALTET = 2 * HERZSCHLAG    # aelter -> Prozess laeuft nicht mehr

PLAN_TEILE = ("Montag", "04:30")

ZYKLEN = {"woechentlich": "Wöchentlich", "monatlich": "Monatlich", "aus": "Aus"}
STANDARD_ZYKLUS = "woechentlich"


def zyklus(conn):
    r = conn.execute("SELECT wert FROM einstellungen WHERE schluessel = 'abgleich'").fetchone()
    return r[0] if r and r[0] in ZYKLEN else STANDARD_ZYKLUS


def set_zyklus(conn, wert):
    if wert not in ZYKLEN:
        raise ValueError(wert)
    conn.execute("INSERT OR REPLACE INTO einstellungen (schluessel, wert) VALUES ('abgleich', ?)", (wert,))


def termin_faellig(conn, termin, zyklus_=None):
    """Soll der Timer-Termin `termin` (datetime) wirklich abgleichen?"""
    z = zyklus_ or zyklus(conn)
    if z == "aus":
        return False
    if z == "monatlich":
        # in diesem Monat schon erfolgreich automatisch abgeglichen -> auslassen
        return not conn.execute("SELECT 1 FROM sync_log WHERE ausloeser = 'timer' AND ok = 1 "
                                "AND substr(ts, 1, 7) = ?", (termin.strftime("%Y-%m"),)).fetchone()
    return True


def naechster_lauf(conn, kandidat):
    """Erster Montagstermin ab `kandidat`, an dem laut Zyklus abgeglichen wird; None bei "aus"."""
    z = zyklus(conn)
    if z == "aus":
        return None
    for _ in range(6):
        if termin_faellig(conn, kandidat, z):
            break
        kandidat += datetime.timedelta(days=7)
    return kandidat


def plan_text(z, st):
    """Zeitplan-Text je nach Zyklus; st = Status mit plan und plan_teile (Wochentag, Zeit)."""
    teile = st.get("plan_teile")
    if z == "monatlich" and teile:
        return tr("am ersten {tag} im Monat um {zeit}", tag=tr(teile[0]), zeit=teile[1])
    return st.get("plan")


def mit_zyklus(conn, st):
    """Status von sync.timer_status() bzw. status() um die Einstellung ergaenzen."""
    if st is None:
        return None
    z = zyklus(conn)
    st = dict(st, zyklus=z, plan=plan_text(z, st))
    if z == "aus":
        st["naechster"] = ""
    elif z == "monatlich" and st.get("naechster_dt") and not st.get("nachholen"):
        st["naechster"] = sync.zeitpunkt_text(naechster_lauf(conn, st["naechster_dt"]))
    return st


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
        "plan": tr("jeden {tag} um {zeit}", tag=tr(PLAN_TEILE[0]), zeit=PLAN_TEILE[1]),
        "plan_teile": PLAN_TEILE,
        "naechster_dt": naechster,
        "nachholen": bool(d.get("nachholen")),
        "naechster": sync.zeitpunkt_text(naechster)
                     + (" (" + tr("verpasster Termin wird nachgeholt") + ")" if d.get("nachholen") else ""),
        "letzter": "",
        "verzoegerung": "" if d.get("nachholen") else tr("{n} Min.", n=MAX_VERZOEGERUNG // 60),
        "docker": True,
    }


def _log(text):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "zeitplan:", text, file=sys.stderr, flush=True)


def _letzter_lauf(db_path):
    with db.session(db_path) as conn:
        r = conn.execute("SELECT ts FROM sync_log WHERE ausloeser = 'timer' ORDER BY id DESC LIMIT 1").fetchone()
    return r["ts"] if r else None


def _termin_faellig(db_path, termin):
    with db.session(db_path) as conn:
        return termin_faellig(conn, termin)


def _abgleichen(db_path, termin):
    if not _termin_faellig(db_path, termin):
        _log("Abgleich ausgelassen (Einstellung: " + ("aus" if _zyklus(db_path) == "aus"
                                                      else "monatlich, diesen Monat schon gelaufen") + ")")
        return
    try:
        with db.session(db_path) as conn:
            res = sync.run(conn, ausloeser="timer")
        _log(f"Abgleich {'ok' if res['ok'] else 'FEHLER'}: {res['gefunden']} gefunden, {res['neu']} neu"
             + (f" - {res['meldung']}" if res.get("meldung") else ""))
    except Exception as ex:                      # der Prozess soll weiterlaufen
        _log(f"Abgleich abgebrochen: {ex!r}")


def _zyklus(db_path):
    with db.session(db_path) as conn:
        return zyklus(conn)


def laufen(db_path):
    """Endlosschleife (manage.py zeitplan)."""
    pfad = datei(db_path)
    jetzt = datetime.datetime.now()
    nachholen = faellig(_letzter_lauf(db_path), jetzt) and _termin_faellig(db_path, letzter_termin(jetzt))
    if nachholen:
        ziel = jetzt + datetime.timedelta(seconds=START_PAUSE)
        _log("Termin verpasst, Abgleich wird nachgeholt")
    else:
        ziel = naechster_termin(jetzt) + datetime.timedelta(seconds=random.randint(0, MAX_VERZOEGERUNG))
    while True:
        jetzt = datetime.datetime.now()
        if jetzt >= ziel:
            _abgleichen(db_path, letzter_termin(jetzt))
            nachholen = False
            jetzt = datetime.datetime.now()
            ziel = naechster_termin(jetzt) + datetime.timedelta(seconds=random.randint(0, MAX_VERZOEGERUNG))
        _schreiben(pfad, ziel, jetzt, nachholen)
        time.sleep(max(1, min(HERZSCHLAG, (ziel - jetzt).total_seconds())))
