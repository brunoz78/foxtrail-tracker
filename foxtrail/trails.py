# -*- coding: utf-8 -*-
"""Lesen/Schreiben der Trail-Liste (Hauptliste, Archiv, eigene Eintraege, manuelle Trails)."""

import json
import re
import uuid

from . import config
from .db import ARCHIV_COND, now

# Typ wird aus dem Namen abgeleitet: foxtrail.ch zeigt MINI/MAXI nur als Badge im
# Titelbild (kein HTML-Element), im Trail-Namen steht es aber immer ("Baccara Mini",
# "Apollon Maxi"). GO erkennt der Scraper am Badge "Digitale Schnitzeljagd".
TYPEN = ("foxtrail", "mini", "maxi", "go")
TYP_LABEL = {"foxtrail": "Foxtrail", "mini": "Mini", "maxi": "Maxi", "go": "GO"}
_TYP_ORDER = {t: i for i, t in enumerate(TYPEN)}
_MAXI_RE = re.compile(r"\bmaxi\b", re.I)
_MINI_RE = re.compile(r"\bmini\b", re.I)

# Schwierigkeitsgrad laut foxtrail.ch (Filter "Schwierigkeit" der Uebersicht); GO-Trails
# haben keinen. NULL in der DB = unbekannt.
GRADE = ("einfach", "mittel", "schwierig")
GRAD_LABEL = {"einfach": "Einfach", "mittel": "Mittel", "schwierig": "Schwierig"}
_GRAD_ORDER = {g: i for i, g in enumerate(GRADE)}

# Anzeigenamen der Regionen (Slug aus der Kategorie-Klasse auf foxtrail.ch)
REGION_LABEL = {
    "aargau": "Aargau", "basel-und-umgebung": "Basel und Umgebung",
    "bern-und-umgebung": "Bern und Umgebung", "graubuenden": "Graubünden", "jura": "Jura",
    "liechtenstein": "Liechtenstein", "luzern-und-umgebung": "Luzern und Umgebung",
    "ostschweiz": "Ostschweiz", "tessin": "Tessin", "wallis": "Wallis",
    "westschweiz": "Westschweiz", "zuerich-und-umgebung": "Zürich und Umgebung",
}

_DAUER_RE = re.compile(r"(\d+(?:[.,]\d+)?)(?:\s*[-–]\s*(\d+(?:[.,]\d+)?))?")


class TrailError(Exception):
    pass


def typ_aus_name(name, go=False):
    """'Baccara Mini' -> 'mini', 'Apollon Maxi' -> 'maxi', GO-Badge -> 'go', sonst 'foxtrail'."""
    if go:
        return "go"
    if _MAXI_RE.search(name or ""):
        return "maxi"
    if _MINI_RE.search(name or ""):
        return "mini"
    return "foxtrail"


def region_label(slug):
    if not slug:
        return ""
    return REGION_LABEL.get(slug) or slug.replace("-", " ").title()


def parse_dauer(s):
    """'1.5-2.5 Stunden' -> (1.5, 2.5); '2 Stunden' -> (2.0, 2.0); unbekannt -> (None, None)."""
    m = _DAUER_RE.search(s or "")
    if not m:
        return (None, None)
    von = float(m.group(1).replace(",", "."))
    bis = float(m.group(2).replace(",", ".")) if m.group(2) else von
    return (von, bis)


def dauer_kurz(s):
    """Anzeige: '1.5-2.5 Stunden' -> '1.5–2.5 h'."""
    s = re.sub(r"\s*Stunden?\b", " h", (s or "").strip())
    return re.sub(r"(\d)\s*-\s*(\d)", r"\1–\2", s)


def _row(r):
    d = dict(r)
    d["archiviert"] = bool(d["quelle"] == "foxtrail" and not d["im_angebot"] and not d["gemacht"])
    d["typ_label"] = TYP_LABEL.get(d["typ"], d["typ"])
    d["region_label"] = region_label(d["region"])
    d["dauer_von"], d["dauer_bis"] = parse_dauer(d["dauer"])
    d["neu"] = bool(d.get("neu_seit"))      # "Neu ab MM/JJ": bleibt, solange neu_seit gesetzt ist
    d["grad_label"] = GRAD_LABEL.get(d.get("schwierigkeit") or "", "")
    return d


def get(conn, trail_id):
    r = conn.execute("SELECT * FROM trails WHERE id = ?", (trail_id,)).fetchone()
    return _row(r) if r else None


# ---- Sortierung (in Python, die Liste hat ~100 Zeilen) ------------------------- #
SORTS = {
    "ort": lambda t: (t["ort"].lower(), t["name"].lower()),
    "name": lambda t: (t["name"].lower(), t["ort"].lower()),
    "region": lambda t: (t["region_label"].lower(), t["ort"].lower(), t["name"].lower()),
    "typ": lambda t: (_TYP_ORDER.get(t["typ"], 99), t["ort"].lower(), t["name"].lower()),
    "schwierigkeit": lambda t: ((_GRAD_ORDER[t["schwierigkeit"]], t["ort"].lower(), t["name"].lower())
                                if t.get("schwierigkeit") in _GRAD_ORDER else None),
    "bewertung": lambda t: t["bewertung"],
    "dauer": lambda t: (t["dauer_von"], t["dauer_bis"]) if t["dauer_von"] is not None else None,
    "preis": lambda t: t["preis"],
    "datum": lambda t: t["gemacht_datum"],
    "neu": lambda t: t.get("neu_seit"),
}


def sortiert(rows, sort="ort", richtung="asc"):
    """Leere Werte (keine Bewertung, kein Datum, ...) stehen immer am Ende."""
    key = SORTS.get(sort) or SORTS["ort"]
    mit = [t for t in rows if key(t) is not None]
    ohne = [t for t in rows if key(t) is None]
    mit.sort(key=key, reverse=(richtung == "desc"))
    return mit + ohne


def _liste(v):
    if v is None or v == "":
        return []
    return [v] if isinstance(v, str) else [x for x in v if x]


def list_active(conn, filter_="alle", q="", typ=(), region=(), dauer=(), grad=(), sort="ort",
                richtung="asc"):
    """Hauptliste: alles ausser Archiv. filter_: alle | offen | gemacht | neu.
    typ/region/dauer/grad: Mehrfachauswahl (Liste oder einzelner Wert), leer = alle."""
    sql = f"SELECT * FROM trails WHERE NOT {ARCHIV_COND}"
    args = []
    if filter_ == "offen":
        sql += " AND gemacht = 0"
    elif filter_ == "gemacht":
        sql += " AND gemacht = 1"
    elif filter_ == "neu":
        sql += " AND neu_seit IS NOT NULL"
    for spalte, werte in (("typ", [t for t in _liste(typ) if t in TYPEN]),
                          ("region", _liste(region)), ("dauer", _liste(dauer)),
                          ("schwierigkeit", [g for g in _liste(grad) if g in GRADE])):
        if werte:
            sql += f" AND {spalte} IN ({','.join('?' * len(werte))})"
            args += werte
    if q:
        sql += " AND (ort LIKE ? OR name LIKE ? OR route LIKE ? OR bemerkung LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY ort COLLATE NOCASE, name COLLATE NOCASE"
    return sortiert([_row(r) for r in conn.execute(sql, args)], sort, richtung)


def filter_options(conn):
    """Werte fuer die Filter in den Spaltenkoepfen: [(wert, anzeige), ...]."""
    regs = [r[0] for r in conn.execute(
        f"SELECT DISTINCT region FROM trails WHERE NOT {ARCHIV_COND} AND region != ''")]
    regs.sort(key=lambda s: region_label(s).lower())
    dauern = [r[0] for r in conn.execute(
        f"SELECT DISTINCT dauer FROM trails WHERE NOT {ARCHIV_COND} AND dauer != ''")]
    dauern.sort(key=lambda s: (parse_dauer(s)[0] is None, parse_dauer(s)[0] or 0,
                               parse_dauer(s)[1] or 0, s))
    return {"region": [(s, region_label(s)) for s in regs],
            "typ": [(t, TYP_LABEL[t]) for t in TYPEN],
            "dauer": [(s, dauer_kurz(s)) for s in dauern],
            "grad": [(g, GRAD_LABEL[g]) for g in GRADE]}


def list_archiv(conn):
    return [_row(r) for r in conn.execute(
        f"SELECT * FROM trails WHERE {ARCHIV_COND} ORDER BY ort COLLATE NOCASE, name COLLATE NOCASE")]


def stats(conn):
    s = {}
    s["total"] = conn.execute(f"SELECT COUNT(*) FROM trails WHERE NOT {ARCHIV_COND}").fetchone()[0]
    s["gemacht"] = conn.execute("SELECT COUNT(*) FROM trails WHERE gemacht = 1").fetchone()[0]
    s["offen"] = conn.execute(
        f"SELECT COUNT(*) FROM trails WHERE gemacht = 0 AND NOT {ARCHIV_COND}").fetchone()[0]
    s["archiv"] = conn.execute(f"SELECT COUNT(*) FROM trails WHERE {ARCHIV_COND}").fetchone()[0]
    s["im_angebot"] = conn.execute("SELECT COUNT(*) FROM trails WHERE im_angebot = 1").fetchone()[0]
    s["neu"] = conn.execute(
        f"SELECT COUNT(*) FROM trails WHERE neu_seit IS NOT NULL AND NOT {ARCHIV_COND}").fetchone()[0]
    s["mitspieler"] = conn.execute(
        "SELECT COALESCE(SUM(mitspieler),0) FROM trails WHERE gemacht = 1").fetchone()[0]
    return s


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_done(form, username):
    gemacht = 1 if form.get("gemacht") in ("1", "on", "ja") else 0
    datum = (form.get("gemacht_datum") or "").strip() or None
    if datum and not _DATE_RE.match(datum):
        raise TrailError("Datum bitte als JJJJ-MM-TT eingeben.")
    mit = (form.get("mitspieler") or "").strip()
    if mit:
        try:
            mit = int(mit)
        except ValueError:
            raise TrailError("Anzahl Mitspieler muss eine ganze Zahl sein.")
        if mit < 1 or mit > 999:
            raise TrailError("Anzahl Mitspieler: 1 bis 999.")
    else:
        mit = None
    bemerkung = (form.get("bemerkung") or "").strip()[:2000]
    if not gemacht:
        datum, mit = None, None
    return gemacht, datum, mit, bemerkung


def update_done(conn, trail_id, form, username):
    """Eigene Eintraege speichern: gemacht, Datum, Mitspieler, Bemerkung."""
    t = get(conn, trail_id)
    if not t:
        raise TrailError("Trail nicht gefunden.")
    gemacht, datum, mit, bemerkung = _clean_done(form, username)
    conn.execute(
        "UPDATE trails SET gemacht=?, gemacht_datum=?, mitspieler=?, bemerkung=?, "
        "erfasst_von=?, erfasst_am=? WHERE id=?",
        (gemacht, datum, mit, bemerkung, username, now(), trail_id))
    return get(conn, trail_id)


def _typ_aus_form(form, name):
    return form.get("typ") if form.get("typ") in TYPEN else typ_aus_name(name)


def _grad_aus_form(form):
    return form.get("schwierigkeit") if form.get("schwierigkeit") in GRADE else None


def _region_aus_form(form):
    return form.get("region") if form.get("region") in REGION_LABEL else ""


def add_manual(conn, form, username):
    """Manuell erfasster Trail (z. B. frueher gemacht, heute nicht mehr im Angebot)."""
    ort = (form.get("ort") or "").strip()[:100]
    name = (form.get("name") or "").strip()[:100]
    if not ort or not name:
        raise TrailError("Ort und Name sind Pflichtfelder.")
    typ = _typ_aus_form(form, name)
    gemacht, datum, mit, bemerkung = _clean_done(form, username)
    slug = "manual-" + uuid.uuid4().hex[:12]
    ts = now()
    conn.execute(
        "INSERT INTO trails (slug, quelle, ort, name, route, typ, region, dauer, schwierigkeit, "
        "im_angebot, first_seen, gemacht, gemacht_datum, mitspieler, bemerkung, erfasst_von, erfasst_am) "
        "VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)",
        (slug, "manual", ort, name, (form.get("route") or "").strip()[:300], typ,
         _region_aus_form(form), (form.get("dauer") or "").strip()[:50], _grad_aus_form(form), ts,
         gemacht, datum, mit, bemerkung, username, ts))
    return conn.execute("SELECT id FROM trails WHERE slug = ?", (slug,)).fetchone()[0]


def update_manual(conn, trail_id, form):
    t = get(conn, trail_id)
    if not t or t["quelle"] != "manual":
        raise TrailError("Nur manuell erfasste Trails koennen so bearbeitet werden.")
    ort = (form.get("ort") or "").strip()[:100]
    name = (form.get("name") or "").strip()[:100]
    if not ort or not name:
        raise TrailError("Ort und Name sind Pflichtfelder.")
    conn.execute("UPDATE trails SET ort=?, name=?, route=?, typ=?, region=?, dauer=?, schwierigkeit=? "
                 "WHERE id=?",
                 (ort, name, (form.get("route") or "").strip()[:300], _typ_aus_form(form, name),
                  _region_aus_form(form), (form.get("dauer") or "").strip()[:50], _grad_aus_form(form),
                  trail_id))


def delete_manual(conn, trail_id):
    t = get(conn, trail_id)
    if not t or t["quelle"] != "manual":
        raise TrailError("Nur manuell erfasste Trails koennen geloescht werden.")
    conn.execute("DELETE FROM trails WHERE id = ?", (trail_id,))


def seed_from_file(conn, path=None):
    """Erstbefuellung aus data/trails_seed.json.

    Fuegt nur Trails ein, die noch nicht existieren, und archiviert nichts - der
    Seed ist eine Momentaufnahme und darf eine aktuellere DB nicht zurueckdrehen.
    Einzige Ausnahme: im Seed hinterlegte Werte fuer neu_seit (aus den Neuigkeiten auf
    foxtrail.ch) und schwierigkeit werden bei vorhandenen Trails nachgetragen, wenn dort
    noch nichts steht - nie ueberschrieben.
    Gibt die Anzahl neu eingefuegter Trails zurueck."""
    with open(path or config.SEED_FILE, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data["trails"] if isinstance(data, dict) else data
    ts = now()
    n = 0
    for t in items:
        neu_seit = t.get("neu_seit") if _DATE_RE.match(t.get("neu_seit") or "") else None
        grad = t.get("schwierigkeit") if t.get("schwierigkeit") in GRADE else None
        if conn.execute("SELECT 1 FROM trails WHERE slug = ?", (t["slug"],)).fetchone():
            if neu_seit:
                conn.execute("UPDATE trails SET neu_seit = ? WHERE slug = ? AND neu_seit IS NULL",
                             (neu_seit, t["slug"]))
            if grad:
                conn.execute("UPDATE trails SET schwierigkeit = ? WHERE slug = ? AND schwierigkeit IS NULL",
                             (grad, t["slug"]))
            continue
        conn.execute(
            "INSERT INTO trails (slug, quelle, ort, name, route, typ, region, bewertung, dauer, "
            "preis, url, schwierigkeit, neu_seit, im_angebot, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (t["slug"], "foxtrail", t["ort"], t["name"], t.get("route", ""),
             typ_aus_name(t["name"], go=(t.get("typ") == "go")) if t.get("typ") in (None, "foxtrail", "go")
             else t["typ"],
             t.get("region", ""), t.get("bewertung"), t.get("dauer", ""), t.get("preis"),
             t.get("url", ""), grad, neu_seit, ts, ts))
        n += 1
    return n
