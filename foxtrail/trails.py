# -*- coding: utf-8 -*-
"""Lesen/Schreiben der Trail-Liste (Hauptliste, Archiv, eigene Eintraege, manuelle Trails)."""

import json
import re
import uuid

from . import config
from .db import ARCHIV_COND, now

TYP_LABEL = {"foxtrail": "Foxtrail", "go": "Foxtrail GO"}


class TrailError(Exception):
    pass


def _row(r):
    d = dict(r)
    d["archiviert"] = bool(d["quelle"] == "foxtrail" and not d["im_angebot"] and not d["gemacht"])
    d["typ_label"] = TYP_LABEL.get(d["typ"], d["typ"])
    return d


def get(conn, trail_id):
    r = conn.execute("SELECT * FROM trails WHERE id = ?", (trail_id,)).fetchone()
    return _row(r) if r else None


def list_active(conn, filter_="alle", q="", typ=""):
    """Hauptliste: alles ausser Archiv. filter_: alle | offen | gemacht."""
    sql = f"SELECT * FROM trails WHERE NOT {ARCHIV_COND}"
    args = []
    if filter_ == "offen":
        sql += " AND gemacht = 0"
    elif filter_ == "gemacht":
        sql += " AND gemacht = 1"
    if typ in ("foxtrail", "go"):
        sql += " AND typ = ?"
        args.append(typ)
    if q:
        sql += " AND (ort LIKE ? OR name LIKE ? OR route LIKE ? OR bemerkung LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY ort COLLATE NOCASE, name COLLATE NOCASE"
    return [_row(r) for r in conn.execute(sql, args)]


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


def add_manual(conn, form, username):
    """Manuell erfasster Trail (z. B. frueher gemacht, heute nicht mehr im Angebot)."""
    ort = (form.get("ort") or "").strip()[:100]
    name = (form.get("name") or "").strip()[:100]
    if not ort or not name:
        raise TrailError("Ort und Name sind Pflichtfelder.")
    typ = form.get("typ") if form.get("typ") in ("foxtrail", "go") else "foxtrail"
    gemacht, datum, mit, bemerkung = _clean_done(form, username)
    slug = "manual-" + uuid.uuid4().hex[:12]
    ts = now()
    conn.execute(
        "INSERT INTO trails (slug, quelle, ort, name, route, typ, region, dauer, im_angebot, "
        "first_seen, gemacht, gemacht_datum, mitspieler, bemerkung, erfasst_von, erfasst_am) "
        "VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)",
        (slug, "manual", ort, name, (form.get("route") or "").strip()[:300], typ, "",
         (form.get("dauer") or "").strip()[:50], ts, gemacht, datum, mit, bemerkung, username, ts))
    return conn.execute("SELECT id FROM trails WHERE slug = ?", (slug,)).fetchone()[0]


def update_manual(conn, trail_id, form):
    t = get(conn, trail_id)
    if not t or t["quelle"] != "manual":
        raise TrailError("Nur manuell erfasste Trails koennen so bearbeitet werden.")
    ort = (form.get("ort") or "").strip()[:100]
    name = (form.get("name") or "").strip()[:100]
    if not ort or not name:
        raise TrailError("Ort und Name sind Pflichtfelder.")
    typ = form.get("typ") if form.get("typ") in ("foxtrail", "go") else "foxtrail"
    conn.execute("UPDATE trails SET ort=?, name=?, route=?, typ=?, dauer=? WHERE id=?",
                 (ort, name, (form.get("route") or "").strip()[:300], typ,
                  (form.get("dauer") or "").strip()[:50], trail_id))


def delete_manual(conn, trail_id):
    t = get(conn, trail_id)
    if not t or t["quelle"] != "manual":
        raise TrailError("Nur manuell erfasste Trails koennen geloescht werden.")
    conn.execute("DELETE FROM trails WHERE id = ?", (trail_id,))


def seed_from_file(conn, path=None):
    """Erstbefuellung aus data/trails_seed.json.

    Fuegt nur Trails ein, die noch nicht existieren, und archiviert nichts - der
    Seed ist eine Momentaufnahme und darf eine aktuellere DB nicht zurueckdrehen.
    Gibt die Anzahl neu eingefuegter Trails zurueck."""
    with open(path or config.SEED_FILE, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data["trails"] if isinstance(data, dict) else data
    ts = now()
    n = 0
    for t in items:
        if conn.execute("SELECT 1 FROM trails WHERE slug = ?", (t["slug"],)).fetchone():
            continue
        conn.execute(
            "INSERT INTO trails (slug, quelle, ort, name, route, typ, region, bewertung, dauer, "
            "preis, url, im_angebot, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (t["slug"], "foxtrail", t["ort"], t["name"], t.get("route", ""), t.get("typ", "foxtrail"),
             t.get("region", ""), t.get("bewertung"), t.get("dauer", ""), t.get("preis"),
             t.get("url", ""), ts, ts))
        n += 1
    return n
