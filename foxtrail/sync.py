# -*- coding: utf-8 -*-
"""
Abgleich der lokalen Trail-Liste mit foxtrail.ch.

Regeln (siehe README):
  * neuer Trail auf der Website            -> anlegen (offen, aktiv, neu_seit = heute,
                                              in der Liste "Neu ab MM/JJ")
  * bekannter Trail weiterhin gelistet     -> Metadaten aktualisieren, eigene
                                              Eintraege (gemacht/Datum/Mitspieler/
                                              Bemerkung) bleiben unangetastet
  * bekannter Trail nicht mehr gelistet    -> im_angebot = 0
        - bereits gemacht:  bleibt in der Hauptliste ("nicht mehr im Angebot")
        - noch offen:       erscheint im Archiv (abgeleitet, siehe db.ARCHIV_COND)
  * archivierter Trail taucht wieder auf   -> im_angebot = 1 (reaktiviert)
  * manuell erfasste Trails (quelle='manual') werden vom Abgleich nie beruehrt
  * Umzug: foxtrail.ch aendert manchmal nur den Regionsteil der Adresse
    (zuerich-und-umgebung/baccara -> ostschweiz/baccara). Ein "neuer" Slug mit gleichem
    letztem Adressteil und gleichem Namen wie ein bekannter, nicht mehr gelisteter Trail
    ist derselbe Trail: der bestehende Eintrag bekommt den neuen Slug (eigene Eintraege,
    Fotos usw. bleiben), statt dass ein Doppel entsteht und der alte ins Archiv wandert.
  * Es wird NIE ein Trail geloescht.

Protokoll: Jeder Lauf schreibt in sync_log.aenderungen (JSON), was sich geaendert hat - neue,
geaenderte (mit altem und neuem Wert je Feld), wieder angebotene, archivierte und gemachte, die
nicht mehr angeboten werden. Die Seite Abgleich zeigt das je Lauf an.

Sicherung: liefert der Scraper deutlich weniger Trails als bisher im Angebot
(< 50 %), wird der Abgleich abgebrochen, damit ein Website-Umbau nicht die
halbe Liste ins Archiv schiebt.
"""

import datetime
import json
import re
import subprocess

from .db import now
from .i18n import tr

META_FIELDS = ("ort", "name", "route", "typ", "region", "bewertung", "dauer", "preis", "url",
               "schwierigkeit")
# bild_url wird mitgefuehrt, zaehlt aber nicht als Aenderung (ein neues Titelbild ist keine
# Meldung wert) und None ueberschreibt einen bekannten Wert nie.
MIN_RATIO = 0.5

# Protokoll: Beschriftungen der Felder und Arten von Aenderungen (deutsch; die Seite Abgleich
# uebersetzt sie, CLI und Log nehmen die Schluessel)
FELD_LABEL = {"ort": "Ort", "name": "Name", "route": "Route", "typ": "Typ", "region": "Region",
              "bewertung": "Bewertung", "dauer": "Dauer", "preis": "Preis", "url": "Adresse",
              "schwierigkeit": "Schwierigkeit"}
ARTEN = {"neu": "Neu", "aktualisiert": "Geändert", "reaktiviert": "Wieder im Angebot",
         "archiviert": "Ins Archiv", "weg": "Gemacht, nicht mehr im Angebot"}


class SyncAbort(Exception):
    pass


def _kurz(slug):
    return slug.rsplit("/", 1)[-1]


def umzug_von(kandidaten, slug, name):
    """Bekannter Trail, der unter `slug` umgezogen ist: gleicher letzter Adressteil, gleicher
    Name (Gross/klein egal). kandidaten = dicts mit slug/name. Nur eindeutige Treffer."""
    treffer = [k for k in kandidaten if k["slug"] != slug and _kurz(k["slug"]) == _kurz(slug)
               and (k["name"] or "").casefold() == (name or "").casefold()]
    return treffer[0] if len(treffer) == 1 else None


def apply(conn, scraped, ausloeser="manual"):
    """Wendet die gescrapte Liste auf die DB an und schreibt einen sync_log-Eintrag.
    Gibt das Ergebnis-dict zurueck. scraped = Liste von dicts (siehe scraper.fetch_all)."""
    ts = now()
    res = {"gefunden": len(scraped), "neu": 0, "aktualisiert": 0, "reaktiviert": 0,
           "archiviert": 0, "nicht_mehr_im_angebot": 0, "meldung": "", "aenderungen": []}
    try:
        _apply(conn, scraped, ts, res)
        ok = 1
    except SyncAbort as ex:
        ok = 0
        res["meldung"] = str(ex)
        res["aenderungen"] = []
    conn.execute(
        "INSERT INTO sync_log (ts, ausloeser, ok, gefunden, neu, aktualisiert, reaktiviert, "
        "archiviert, nicht_mehr_im_angebot, meldung, aenderungen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ts, ausloeser, ok, res["gefunden"], res["neu"], res["aktualisiert"], res["reaktiviert"],
         res["archiviert"], res["nicht_mehr_im_angebot"], res["meldung"],
         json.dumps(res["aenderungen"], ensure_ascii=False)))
    res["ok"] = bool(ok)
    return res


def _apply(conn, scraped, ts, res):
    slugs = {t["slug"] for t in scraped}
    if not slugs:
        raise SyncAbort("Leere Liste vom Scraper - Abgleich abgebrochen.")
    current = conn.execute(
        "SELECT COUNT(*) FROM trails WHERE quelle = 'foxtrail' AND im_angebot = 1").fetchone()[0]
    if current and len(slugs) < current * MIN_RATIO:
        raise SyncAbort(f"Nur {len(slugs)} Trails gefunden, bisher {current} im Angebot - "
                        "Abgleich sicherheitshalber abgebrochen.")

    existing = {r["slug"]: dict(r) for r in
                conn.execute("SELECT * FROM trails WHERE quelle = 'foxtrail'")}

    umzuege = []
    for t in scraped:
        old = existing.get(t["slug"])
        if old is None:
            weg = [e for s, e in existing.items() if s not in slugs]
            old = umzug_von(weg, t["slug"], t["name"])
            if old is not None:
                conn.execute("UPDATE trails SET slug = ? WHERE id = ?", (t["slug"], old["id"]))
                del existing[old["slug"]]
                umzuege.append(f"{old['name']}: {old['slug']} → {t['slug']}")
                old["slug"] = t["slug"]
                existing[t["slug"]] = old
        if old is None:
            conn.execute(
                "INSERT INTO trails (slug, quelle, ort, name, route, typ, region, bewertung, "
                "dauer, preis, url, schwierigkeit, bild_url, im_angebot, first_seen, last_seen, neu_seit) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                (t["slug"], "foxtrail", t["ort"], t["name"], t.get("route", ""), t.get("typ", "foxtrail"),
                 t.get("region", ""), t.get("bewertung"), t.get("dauer", ""), t.get("preis"),
                 t.get("url", ""), t.get("schwierigkeit"), t.get("bild_url"), ts, ts, ts[:10]))
            res["neu"] += 1
            res["aenderungen"].append({"art": "neu", "name": t["name"], "ort": t["ort"]})
            continue
        # Schwierigkeit None = "diesmal nicht ermittelt" (z. B. Filter-Abruf gescheitert):
        # bekannter Wert bleibt, zaehlt nicht als Aenderung.
        felder = [[f, old.get(f), t.get(f)] for f in META_FIELDS
                  if (old.get(f) or "") != (t.get(f) or "")
                  and not (f == "schwierigkeit" and t.get(f) is None)]
        if not old["im_angebot"]:
            res["reaktiviert"] += 1
            res["aenderungen"].append({"art": "reaktiviert", "name": t["name"], "ort": t["ort"],
                                       "felder": felder})
        elif felder:
            res["aktualisiert"] += 1
            res["aenderungen"].append({"art": "aktualisiert", "name": t["name"], "ort": t["ort"],
                                       "felder": felder})
        conn.execute(
            "UPDATE trails SET ort=?, name=?, route=?, typ=?, region=?, bewertung=?, dauer=?, "
            "preis=?, url=?, schwierigkeit=COALESCE(?, schwierigkeit), bild_url=COALESCE(?, bild_url), "
            "im_angebot=1, last_seen=? WHERE id=?",
            (t["ort"], t["name"], t.get("route", ""), t.get("typ", "foxtrail"), t.get("region", ""),
             t.get("bewertung"), t.get("dauer", ""), t.get("preis"), t.get("url", ""),
             t.get("schwierigkeit"), t.get("bild_url"), ts, old["id"]))

    if umzuege:
        res["meldung"] = "Neue Adresse: " + "; ".join(umzuege)

    for slug, old in existing.items():
        if slug in slugs or not old["im_angebot"]:
            continue
        conn.execute("UPDATE trails SET im_angebot = 0 WHERE id = ?", (old["id"],))
        if old["gemacht"]:
            res["nicht_mehr_im_angebot"] += 1
        else:
            res["archiviert"] += 1
        res["aenderungen"].append({"art": "weg" if old["gemacht"] else "archiviert",
                                   "name": old["name"], "ort": old["ort"]})


def aenderung_text(a):
    """Eine Aenderung als Textzeile fuer CLI und Log (Schluessel statt Beschriftungen)."""
    zeile = f"{a['art']}: {a['name']} ({a['ort']})"
    felder = "; ".join(f"{f} {'-' if alt in (None, '') else alt} -> {'-' if neu in (None, '') else neu}"
                       for f, alt, neu in a.get("felder") or [])
    return f"{zeile}: {felder}" if felder else zeile


TIMER = "foxtrail-sync.timer"
_WOCHENTAG = {"Mon": "Montag", "Tue": "Dienstag", "Wed": "Mittwoch", "Thu": "Donnerstag",
              "Fri": "Freitag", "Sat": "Samstag", "Sun": "Sonntag"}
_WOCHENTAG_NR = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_ZEITPUNKT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})")
_ONCAL_RE = re.compile(r"OnCalendar=([^;}]+)")
_WOCHE_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \*-\*-\* (\d{2}):(\d{2})(?::\d{2})?$")


def _zeitpunkt(text):
    """'Mon 2026-09-21 04:30:00 CEST' -> datetime (Ortszeit des Containers), sonst None."""
    m = _ZEITPUNKT_RE.search(text or "")
    if not m:
        return None
    return datetime.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")


def zeitpunkt_text(dt):
    """datetime -> 'Montag, 21.09.2026, 04:30'."""
    return f"{tr(_WOCHENTAG_NR[dt.weekday()])}, {dt:%d.%m.%Y, %H:%M}" if dt else ""


def plan_teile(oncalendar):
    """'Mon *-*-* 04:30:00' -> ('Montag', '04:30'); sonst None."""
    m = _WOCHE_RE.match((oncalendar or "").strip())
    return (_WOCHENTAG[m.group(1)], f"{m.group(2)}:{m.group(3)}") if m else None


def plan_text(oncalendar):
    """'Mon *-*-* 04:30:00' -> 'jeden Montag um 04:30'; Unbekanntes bleibt wie es ist."""
    teile = plan_teile(oncalendar)
    if not teile:
        return (oncalendar or "").strip()
    return tr("jeden {tag} um {zeit}", tag=tr(teile[0]), zeit=teile[1])


def timer_status(ausgabe=None):
    """Zustand des systemd-Timers fuer die Seite Abgleich. None, wenn es keinen gibt
    (lokale Entwicklung, Windows, Timer nicht installiert). `ausgabe` fuer Tests.
    Liest nur (systemctl show), braucht keine Rechte."""
    if ausgabe is None:
        try:
            ausgabe = subprocess.run(
                ["systemctl", "show", TIMER, "-p", "ActiveState", "-p", "LoadState", "-p", "TimersCalendar",
                 "-p", "NextElapseUSecRealtime", "-p", "LastTriggerUSec", "-p", "RandomizedDelayUSec"],
                capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    werte = dict(z.split("=", 1) for z in ausgabe.splitlines() if "=" in z)
    if werte.get("LoadState") != "loaded":
        return None
    oncal = _ONCAL_RE.search(werte.get("TimersCalendar", ""))
    # systemd schreibt '30min', '1h', '1h 30min' -> '30 Min.', '1 Std.', '1 Std. 30 Min.'
    verzoegerung = re.sub(r"(\d+)min", lambda m: tr("{n} Min.", n=m.group(1)), werte.get("RandomizedDelayUSec", ""))
    verzoegerung = re.sub(r"(\d+)h\b", lambda m: tr("{n} Std.", n=m.group(1)), verzoegerung)
    naechster = _zeitpunkt(werte.get("NextElapseUSecRealtime"))
    return {
        "aktiv": werte.get("ActiveState") == "active",
        "plan": plan_text(oncal.group(1)) if oncal else "",
        "plan_teile": plan_teile(oncal.group(1)) if oncal else None,
        "naechster": zeitpunkt_text(naechster),
        "naechster_dt": naechster,
        "letzter": zeitpunkt_text(_zeitpunkt(werte.get("LastTriggerUSec"))),
        "verzoegerung": verzoegerung if verzoegerung not in ("", "0") else "",
    }


def last_runs(conn, limit=20):
    runs = []
    for r in conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        try:
            d["aenderungen"] = json.loads(d.get("aenderungen") or "[]")
        except ValueError:
            d["aenderungen"] = []
        runs.append(d)
    return runs


def run(conn, ausloeser="manual", scraped=None):
    """Kompletter Lauf: scrapen + anwenden. scraped kann fuer Tests vorgegeben werden."""
    from . import scraper
    if scraped is None:
        try:
            scraped = scraper.fetch_all()
        except scraper.ScrapeError as ex:
            ts = now()
            conn.execute(
                "INSERT INTO sync_log (ts, ausloeser, ok, meldung) VALUES (?,?,0,?)",
                (ts, ausloeser, str(ex)))
            return {"ok": False, "gefunden": 0, "neu": 0, "aktualisiert": 0, "reaktiviert": 0,
                    "archiviert": 0, "nicht_mehr_im_angebot": 0, "meldung": str(ex)}
    return apply(conn, scraped, ausloeser)
