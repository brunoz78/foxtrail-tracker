# -*- coding: utf-8 -*-
"""
Abgleich der lokalen Trail-Liste mit foxtrail.ch.

Regeln (siehe README):
  * neuer Trail auf der Website            -> anlegen (offen, aktiv)
  * bekannter Trail weiterhin gelistet     -> Metadaten aktualisieren, eigene
                                              Eintraege (gemacht/Datum/Mitspieler/
                                              Bemerkung) bleiben unangetastet
  * bekannter Trail nicht mehr gelistet    -> im_angebot = 0
        - bereits gemacht:  bleibt in der Hauptliste ("nicht mehr im Angebot")
        - noch offen:       erscheint im Archiv (abgeleitet, siehe db.ARCHIV_COND)
  * archivierter Trail taucht wieder auf   -> im_angebot = 1 (reaktiviert)
  * manuell erfasste Trails (quelle='manual') werden vom Abgleich nie beruehrt
  * Es wird NIE ein Trail geloescht.

Sicherung: liefert der Scraper deutlich weniger Trails als bisher im Angebot
(< 50 %), wird der Abgleich abgebrochen, damit ein Website-Umbau nicht die
halbe Liste ins Archiv schiebt.
"""

from .db import now

META_FIELDS = ("ort", "name", "route", "typ", "region", "bewertung", "dauer", "preis", "url")
MIN_RATIO = 0.5


class SyncAbort(Exception):
    pass


def apply(conn, scraped, ausloeser="manual"):
    """Wendet die gescrapte Liste auf die DB an und schreibt einen sync_log-Eintrag.
    Gibt das Ergebnis-dict zurueck. scraped = Liste von dicts (siehe scraper.fetch_all)."""
    ts = now()
    res = {"gefunden": len(scraped), "neu": 0, "aktualisiert": 0, "reaktiviert": 0,
           "archiviert": 0, "nicht_mehr_im_angebot": 0, "meldung": ""}
    try:
        _apply(conn, scraped, ts, res)
        ok = 1
    except SyncAbort as ex:
        ok = 0
        res["meldung"] = str(ex)
    conn.execute(
        "INSERT INTO sync_log (ts, ausloeser, ok, gefunden, neu, aktualisiert, reaktiviert, "
        "archiviert, nicht_mehr_im_angebot, meldung) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, ausloeser, ok, res["gefunden"], res["neu"], res["aktualisiert"], res["reaktiviert"],
         res["archiviert"], res["nicht_mehr_im_angebot"], res["meldung"]))
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

    for t in scraped:
        old = existing.get(t["slug"])
        if old is None:
            conn.execute(
                "INSERT INTO trails (slug, quelle, ort, name, route, typ, region, bewertung, "
                "dauer, preis, url, im_angebot, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (t["slug"], "foxtrail", t["ort"], t["name"], t.get("route", ""), t.get("typ", "foxtrail"),
                 t.get("region", ""), t.get("bewertung"), t.get("dauer", ""), t.get("preis"),
                 t.get("url", ""), ts, ts))
            res["neu"] += 1
            continue
        changed = any((old.get(f) or "") != (t.get(f) or "") for f in META_FIELDS)
        if not old["im_angebot"]:
            res["reaktiviert"] += 1
        elif changed:
            res["aktualisiert"] += 1
        conn.execute(
            "UPDATE trails SET ort=?, name=?, route=?, typ=?, region=?, bewertung=?, dauer=?, "
            "preis=?, url=?, im_angebot=1, last_seen=? WHERE id=?",
            (t["ort"], t["name"], t.get("route", ""), t.get("typ", "foxtrail"), t.get("region", ""),
             t.get("bewertung"), t.get("dauer", ""), t.get("preis"), t.get("url", ""), ts, old["id"]))

    for slug, old in existing.items():
        if slug in slugs or not old["im_angebot"]:
            continue
        conn.execute("UPDATE trails SET im_angebot = 0 WHERE id = ?", (old["id"],))
        if old["gemacht"]:
            res["nicht_mehr_im_angebot"] += 1
        else:
            res["archiviert"] += 1


def last_runs(conn, limit=20):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,))]


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
