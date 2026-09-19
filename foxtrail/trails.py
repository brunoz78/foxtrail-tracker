# -*- coding: utf-8 -*-
"""Lesen/Schreiben der Trail-Liste (Hauptliste, Archiv, eigene Eintraege, manuelle Trails)."""

import datetime
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

# Spalten der Listenansicht, in Anzeigereihenfolge: (Schluessel, Beschriftung im Auswahlmenue,
# standardmaessig sichtbar). Ort und Trail stehen immer da. Die Auswahl je Benutzer liegt in
# users.spalten (NULL = Standard), siehe spalten_aus().
SPALTEN = (
    ("route", "Route", True), ("startort", "Startort", False), ("zielort", "Zielort", False),
    ("region", "Region", True), ("typ", "Typ", True), ("grad", "Schwierigkeit", True),
    ("bewertung", "Bewertung ★", True), ("dauer", "Dauer", True), ("preis", "Preis CHF", True),
    ("neu", "Neu ab", False), ("datum", "Datum gemacht", True), ("start", "Startzeit", False),
    ("ziel", "Zielzeit", False), ("zeit", "Spielzeit", True), ("pers", "Mitspieler", True),
    ("team", "Team-Code", False), ("bestellung", "Bestellnummer", False),
    ("bemerkung", "Bemerkung", True), ("erfasst", "Erfasst von", False),
)
SPALTEN_KEYS = tuple(k for k, _, _ in SPALTEN)
SPALTEN_STANDARD = tuple(k for k, _, an in SPALTEN if an)
_ROUTE_TEIL_RE = re.compile(r"\s+[-–]\s+")

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


def spalten_aus(wert):
    """Gespeicherte Auswahl ('route,typ,...' oder Liste) -> gueltige Schluessel in Anzeige-
    reihenfolge. None -> Standard, "" -> keine Zusatzspalten. Unbekannte werden ignoriert."""
    if wert is None:
        return list(SPALTEN_STANDARD)
    gewaehlt = set(wert.split(",") if isinstance(wert, str) else wert)
    return [k for k in SPALTEN_KEYS if k in gewaehlt]


def route_enden(route):
    """'Saas-Grund - Saas-Fee - Felskinn' -> ('Saas-Grund', 'Felskinn'). Die Route auf
    foxtrail.ch nennt die Stationen vom Start bis zum Ziel."""
    teile = [p.strip() for p in _ROUTE_TEIL_RE.split(route or "") if p.strip()]
    if not teile:
        return ("", "")
    return (teile[0], teile[-1] if len(teile) > 1 else "")


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


def spielzeit_min(start, ziel):
    """'JJJJ-MM-TT HH:MM' x2 -> Minuten zwischen Start und Ziel; None wenn unvollstaendig/unsinnig."""
    try:
        a = datetime.datetime.strptime(start or "", "%Y-%m-%d %H:%M")
        b = datetime.datetime.strptime(ziel or "", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    m = int((b - a).total_seconds() // 60)
    return m if m > 0 else None


def spielzeit_label(minuten):
    return f"{minuten // 60}:{minuten % 60:02d} h" if minuten else ""


def _row(r):
    d = dict(r)
    d["archiviert"] = bool(d["quelle"] == "foxtrail" and not d["im_angebot"] and not d["gemacht"])
    d["typ_label"] = TYP_LABEL.get(d["typ"], d["typ"])
    d["region_label"] = region_label(d["region"])
    d["dauer_von"], d["dauer_bis"] = parse_dauer(d["dauer"])
    d["neu"] = bool(d.get("neu_seit"))      # "Neu ab MM/JJ": bleibt, solange neu_seit gesetzt ist
    d["grad_label"] = GRAD_LABEL.get(d.get("schwierigkeit") or "", "")
    d["spielzeit_min"] = spielzeit_min(d.get("start_zeit"), d.get("ziel_zeit"))
    d["spielzeit"] = spielzeit_label(d["spielzeit_min"])
    d["startort"], d["zielort"] = route_enden(d.get("route"))
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
    "spielzeit": lambda t: t["spielzeit_min"],
    "startort": lambda t: (t["startort"].lower(), t["ort"].lower()) if t["startort"] else None,
    "zielort": lambda t: (t["zielort"].lower(), t["ort"].lower()) if t["zielort"] else None,
    "start": lambda t: t.get("start_zeit"),
    "ziel": lambda t: t.get("ziel_zeit"),
    "gesehen": lambda t: t.get("last_seen"),
    "erfasst": lambda t: ((t["erfasst_von"].lower(), t["ort"].lower(), t["name"].lower())
                          if t.get("erfasst_von") else None),
}
LEER = "-"         # Filterwert "ohne Angabe" (Grad, Region, Dauer, Erfasst von leer)
ARCHIV_SORTS = ("ort", "name", "region", "gesehen")


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


def list_active(conn, filter_="alle", q="", typ=(), region=(), dauer=(), grad=(), erfasst=(), sort="ort",
                richtung="asc"):
    """Hauptliste: alles ausser Archiv. filter_: alle | offen | gemacht | neu.
    typ/region/dauer/grad/erfasst: Mehrfachauswahl (Liste oder einzelner Wert), leer = alle;
    der Wert LEER waehlt Trails ohne Angabe in dieser Spalte."""
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
                          ("schwierigkeit", [g for g in _liste(grad) if g in GRADE or g == LEER]),
                          ("erfasst_von", _liste(erfasst))):
        if werte:
            namen = [w for w in werte if w != LEER]
            teile = [f"{spalte} IN ({','.join('?' * len(namen))})"] if namen else []
            if LEER in werte:
                teile.append(f"COALESCE({spalte}, '') = ''")
            sql += " AND (" + " OR ".join(teile) + ")"
            args += namen
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
    wer = [r[0] for r in conn.execute(
        f"SELECT DISTINCT erfasst_von FROM trails WHERE NOT {ARCHIV_COND} AND COALESCE(erfasst_von, '') != ''")]
    wer.sort(key=str.lower)

    def ohne(spalte, text="– ohne Angabe"):
        """Eintrag "ohne Angabe" am Ende - nur, wenn es solche Trails gibt."""
        n = conn.execute(f"SELECT COUNT(*) FROM trails WHERE NOT {ARCHIV_COND} "
                         f"AND COALESCE({spalte}, '') = ''").fetchone()[0]
        return [(LEER, text)] if n else []

    # typ ist nie leer (Default 'foxtrail'), deshalb dort kein "ohne Angabe"
    return {"erfasst": [(w, w) for w in wer] + ohne("erfasst_von", "– nicht erfasst"),
            "region": [(s, region_label(s)) for s in regs] + ohne("region"),
            "typ": [(t, TYP_LABEL[t]) for t in TYPEN],
            "dauer": [(s, dauer_kurz(s)) for s in dauern] + ohne("dauer"),
            "grad": [(g, GRAD_LABEL[g]) for g in GRADE] + ohne("schwierigkeit")}


def list_archiv(conn, q="", sort="ort", richtung="asc"):
    """Archiv mit Suche (Ort, Name, Route) und Sortierung (ARCHIV_SORTS)."""
    sql, args = f"SELECT * FROM trails WHERE {ARCHIV_COND}", []
    if q:
        sql += " AND (ort LIKE ? OR name LIKE ? OR route LIKE ?)"
        args += [f"%{q}%"] * 3
    sql += " ORDER BY ort COLLATE NOCASE, name COLLATE NOCASE"
    return sortiert([_row(r) for r in conn.execute(sql, args)],
                    sort if sort in ARCHIV_SORTS else "ort", richtung)


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


def _clean_done(form, username, datum_pflicht=False):
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
    elif datum_pflicht and not datum:
        raise TrailError("Bitte das Datum eintragen, an dem ihr den Trail gemacht habt.")
    return gemacht, datum, mit, bemerkung


def update_done(conn, trail_id, form, username, datum_pflicht=False):
    """Eigene Eintraege speichern: gemacht, Datum, Mitspieler, Bemerkung.
    Bei einem noch offenen Trail zaehlt ein eingetragenes Datum oder eine Mitspielerzahl als
    "gemacht", auch ohne Haken (sonst gingen die Angaben stillschweigend verloren). Ein schon
    gemachter Trail laesst sich weiterhin per Haken zuruecksetzen. datum_pflicht (Oberflaeche):
    ein gemachter Trail braucht ein Datum; Importe duerfen ohne."""
    t = get(conn, trail_id)
    if not t:
        raise TrailError("Trail nicht gefunden.")
    if not t["gemacht"] and ((form.get("gemacht_datum") or "").strip() or (form.get("mitspieler") or "").strip()):
        form = dict(form.items(), gemacht="1")
    gemacht, datum, mit, bemerkung = _clean_done(form, username, datum_pflicht)
    # offen und ohne Bemerkung: es gibt keine eigenen Eintraege mehr -> "nicht erfasst"
    leer = not gemacht and not bemerkung
    conn.execute(
        "UPDATE trails SET gemacht=?, gemacht_datum=?, mitspieler=?, bemerkung=?, "
        "erfasst_von=?, erfasst_am=? WHERE id=?",
        (gemacht, datum, mit, bemerkung, None if leer else username, None if leer else now(), trail_id))
    return get(conn, trail_id)


def zuruecksetzen(conn, trail_id):
    """Alle eigenen Eintraege eines Trails entfernen (gemacht, Datum, Mitspieler, Bemerkung,
    Erfasst von, Importdaten, Foto) - die Trail-Daten von foxtrail.ch bleiben. foto = NULL (nicht
    ''), ein spaeterer Import darf das Schlussfoto also wieder laden. Gibt den alten Foto-Namen
    zurueck, damit der Aufrufer die Datei loescht."""
    t = get(conn, trail_id)
    if not t:
        raise TrailError("Trail nicht gefunden.")
    conn.execute(
        "UPDATE trails SET gemacht=0, gemacht_datum=NULL, mitspieler=NULL, bemerkung='', erfasst_von=NULL, "
        "erfasst_am=NULL, start_zeit=NULL, ziel_zeit=NULL, team_code=NULL, bestellung=NULL, foto_url=NULL, "
        "foto=NULL WHERE id=?", (trail_id,))
    return t.get("foto")


def set_import(conn, trail_id, start=None, ziel=None, code=None, bestellung=None, foto_url=None):
    """Zusatzdaten aus dem Bestellungs-Import; None laesst den bestehenden Wert stehen."""
    conn.execute(
        "UPDATE trails SET start_zeit=COALESCE(?, start_zeit), ziel_zeit=COALESCE(?, ziel_zeit), "
        "team_code=COALESCE(?, team_code), bestellung=COALESCE(?, bestellung), "
        "foto_url=COALESCE(?, foto_url) WHERE id=?",
        (start, ziel, code, bestellung, foto_url, trail_id))


def set_foto(conn, trail_id, dateiname):
    conn.execute("UPDATE trails SET foto=? WHERE id=?", (dateiname, trail_id))


def _typ_aus_form(form, name):
    return form.get("typ") if form.get("typ") in TYPEN else typ_aus_name(name)


def _grad_aus_form(form):
    return form.get("schwierigkeit") if form.get("schwierigkeit") in GRADE else None


def _region_aus_form(form):
    return form.get("region") if form.get("region") in REGION_LABEL else ""


def add_manual(conn, form, username, datum_pflicht=False):
    """Manuell erfasster Trail (z. B. frueher gemacht, heute nicht mehr im Angebot)."""
    ort = (form.get("ort") or "").strip()[:100]
    name = (form.get("name") or "").strip()[:100]
    if not ort or not name:
        raise TrailError("Ort und Name sind Pflichtfelder.")
    typ = _typ_aus_form(form, name)
    gemacht, datum, mit, bemerkung = _clean_done(form, username, datum_pflicht)
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
        raise TrailError("Nur manuell erfasste Trails können so bearbeitet werden.")
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
        raise TrailError("Nur manuell erfasste Trails können gelöscht werden.")
    conn.execute("DELETE FROM trails WHERE id = ?", (trail_id,))


def seed_from_file(conn, path=None):
    """Erstbefuellung aus data/trails_seed.json.

    Fuegt nur Trails ein, die noch nicht existieren, und archiviert nichts - der
    Seed ist eine Momentaufnahme und darf eine aktuellere DB nicht zurueckdrehen.
    Einzige Ausnahme: im Seed hinterlegte Werte fuer neu_seit (aus den Neuigkeiten auf
    foxtrail.ch), schwierigkeit und bild_url werden bei vorhandenen Trails nachgetragen, wenn dort
    noch nichts steht - nie ueberschrieben.

    "ehemalig": Trails, die foxtrail.ch frueher angeboten hat (aus archivierten Seiten im
    Internet Archive). Sie werden mit im_angebot = 0 eingefuegt - offene landen damit im Archiv,
    und "zuletzt gesehen" ist das Datum des juengsten Archivstands. Nur einfuegen, nie aendern;
    taucht einer auf foxtrail.ch wieder auf, reaktiviert ihn der Abgleich. Gibt es schon einen Trail
    mit demselben Namen, wird keiner angelegt; ist es ein von Hand erfasster, werden dort nur
    leere Felder (Route, Dauer, Region, Bewertung, Preis) aus dem Archivstand ergaenzt.

    Ein Seed-Trail, dessen bekannter Eintrag unter altem Slug existiert (Umzug, siehe
    sync.umzug_von), wird nicht doppelt eingefuegt - der naechste Abgleich stellt ihn um.
    Gibt die Anzahl neu eingefuegter Trails zurueck."""
    with open(path or config.SEED_FILE, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data["trails"] if isinstance(data, dict) else data
    ehemalig = data.get("ehemalig", []) if isinstance(data, dict) else []
    # Irrtuemlich aufgenommene ehemalige Trails wieder entfernen - nur solange niemand etwas
    # eingetragen hat (offen, kein Foto, keine Bemerkung) und foxtrail.ch ihn nicht anbietet.
    for slug in (data.get("ehemalig_entfernt", []) if isinstance(data, dict) else []):
        conn.execute("DELETE FROM trails WHERE slug = ? AND quelle = 'foxtrail' AND im_angebot = 0 "
                     "AND gemacht = 0 AND COALESCE(foto, '') = '' AND COALESCE(bemerkung, '') = ''",
                     (slug,))
    ts = now()
    n = 0
    namen = {}
    for r in conn.execute("SELECT id, name, quelle FROM trails"):
        namen.setdefault(r["name"].casefold(), []).append(r)
    for t in ehemalig:
        if conn.execute("SELECT 1 FROM trails WHERE slug = ?", (t["slug"],)).fetchone():
            continue
        if t["name"].casefold() in namen:
            for r in namen[t["name"].casefold()]:
                if r["quelle"] == "manual":
                    conn.execute(
                        "UPDATE trails SET route = CASE WHEN COALESCE(route, '') = '' THEN ? ELSE route END, "
                        "dauer = CASE WHEN COALESCE(dauer, '') = '' THEN ? ELSE dauer END, "
                        "region = CASE WHEN COALESCE(region, '') = '' THEN ? ELSE region END, "
                        "bewertung = COALESCE(bewertung, ?), preis = COALESCE(preis, ?) WHERE id = ?",
                        (t.get("route", ""), t.get("dauer", ""), t.get("region", ""), t.get("bewertung"),
                         t.get("preis"), r["id"]))
            continue
        gesehen = t["zuletzt_gesehen"] + " 00:00:00" if _DATE_RE.match(t.get("zuletzt_gesehen") or "") else ts
        conn.execute(
            "INSERT INTO trails (slug, quelle, ort, name, route, typ, region, bewertung, dauer, "
            "preis, url, schwierigkeit, im_angebot, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
            (t["slug"], "foxtrail", t["ort"], t["name"], t.get("route", ""),
             typ_aus_name(t["name"], go=(t.get("typ") == "go")) if t.get("typ") in (None, "foxtrail", "go")
             else t["typ"],
             t.get("region", ""), t.get("bewertung"), t.get("dauer", ""), t.get("preis"),
             t.get("url", ""), t.get("schwierigkeit") if t.get("schwierigkeit") in GRADE else None,
             gesehen, gesehen))
        n += 1
    from .sync import umzug_von
    bekannte = [dict(r) for r in conn.execute("SELECT slug, name FROM trails WHERE quelle = 'foxtrail'")]
    for t in items:
        neu_seit = t.get("neu_seit") if _DATE_RE.match(t.get("neu_seit") or "") else None
        grad = t.get("schwierigkeit") if t.get("schwierigkeit") in GRADE else None
        bild = t.get("bild_url") if (t.get("bild_url") or "").startswith("https://") else None
        if conn.execute("SELECT 1 FROM trails WHERE slug = ?", (t["slug"],)).fetchone():
            if neu_seit:
                conn.execute("UPDATE trails SET neu_seit = ? WHERE slug = ? AND neu_seit IS NULL",
                             (neu_seit, t["slug"]))
            if grad:
                conn.execute("UPDATE trails SET schwierigkeit = ? WHERE slug = ? AND schwierigkeit IS NULL",
                             (grad, t["slug"]))
            if bild:
                conn.execute("UPDATE trails SET bild_url = ? WHERE slug = ? AND bild_url IS NULL",
                             (bild, t["slug"]))
            continue
        if umzug_von(bekannte, t["slug"], t["name"]):
            continue
        conn.execute(
            "INSERT INTO trails (slug, quelle, ort, name, route, typ, region, bewertung, dauer, "
            "preis, url, schwierigkeit, neu_seit, bild_url, im_angebot, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (t["slug"], "foxtrail", t["ort"], t["name"], t.get("route", ""),
             typ_aus_name(t["name"], go=(t.get("typ") == "go")) if t.get("typ") in (None, "foxtrail", "go")
             else t["typ"],
             t.get("region", ""), t.get("bewertung"), t.get("dauer", ""), t.get("preis"),
             t.get("url", ""), grad, neu_seit, bild, ts, ts))
        n += 1
    return n


# ---- Statistik ---------------------------------------------------------------- #
def _anteile(alle, feld, reihenfolge, label):
    """[(wert, anzeige, gemacht, gesamt)] je Auspraegung von `feld`, leere Werte ausgelassen."""
    zaehler = {}
    for t in alle:
        v = t.get(feld) or ""
        if v:
            z = zaehler.setdefault(v, [0, 0])
            z[0] += 1 if t["gemacht"] else 0
            z[1] += 1
    werte = [v for v in reihenfolge if v in zaehler] if reihenfolge else \
        sorted(zaehler, key=lambda v: (-zaehler[v][0], label(v).lower()))
    return [(v, label(v), zaehler[v][0], zaehler[v][1]) for v in werte]


def statistik(conn):
    """Kennzahlen fuer die Seite /statistik. Grundlage: Hauptliste (ohne Archiv) - gemachte
    Trails stehen immer dort, auch wenn sie nicht mehr angeboten werden."""
    alle = [_row(r) for r in conn.execute(f"SELECT * FROM trails WHERE NOT {ARCHIV_COND}")]
    gemacht = [t for t in alle if t["gemacht"]]
    datiert = sorted((t for t in gemacht if _DATE_RE.match(t["gemacht_datum"] or "")),
                     key=lambda t: t["gemacht_datum"])
    jahre = {}
    for t in datiert:
        j = int(t["gemacht_datum"][:4])
        jahre[j] = jahre.get(j, 0) + 1
    if jahre:                                   # Luecken als 0 zeigen, damit die Zeitachse stimmt
        jahre = {j: jahre.get(j, 0) for j in range(min(jahre), max(jahre) + 1)}
    mit_zeit = sorted((t for t in gemacht if t["spielzeit_min"]), key=lambda t: t["spielzeit_min"])
    minuten = sum(t["spielzeit_min"] for t in mit_zeit)
    regionen = _anteile(alle, "region", None, region_label)     # sortiert: meiste gemachte zuerst
    besucht = [r for r in regionen if r[2]]
    jahr = datetime.date.today().year
    return {
        "gemacht": len(gemacht), "gesamt": len(alle),
        "ohne_datum": len(gemacht) - len(datiert),
        "erster": datiert[0] if datiert else None, "letzter": datiert[-1] if datiert else None,
        "jahre": sorted(jahre.items()),
        "dieses_jahr": jahre.get(jahr, 0), "jahr": jahr,
        # Summe der Mitspieler sagt wenig, wenn immer dieselben mitspielen - stattdessen,
        # wie viele Regionen schon erkundet sind (Wunsch von Bruno, 2026-09-18)
        "regionen_besucht": len(besucht), "regionen_gesamt": len(regionen),
        "top_region": besucht[0] if besucht else None,
        "spielzeit_n": len(mit_zeit),
        "spielzeit_summe": spielzeit_label(minuten),
        "spielzeit_schnitt": spielzeit_label(round(minuten / len(mit_zeit))) if mit_zeit else "",
        "schnellster": mit_zeit[0] if mit_zeit else None,
        "laengster": mit_zeit[-1] if len(mit_zeit) > 1 else None,
        "regionen": regionen,
        "typen": _anteile(alle, "typ", TYPEN, lambda v: TYP_LABEL.get(v, v)),
        "grade": _anteile(alle, "schwierigkeit", GRADE, lambda v: GRAD_LABEL.get(v, v)),
    }
