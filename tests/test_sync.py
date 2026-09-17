# -*- coding: utf-8 -*-
"""Tests der Abgleich-Regeln (ohne Netz, In-Memory-SQLite)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxtrail import db, sync, trails  # noqa: E402


def mk(slug, **kw):
    d = {"slug": slug, "ort": "Ort", "name": slug.split("/")[-1].title(), "route": "A - B",
         "typ": "foxtrail", "region": slug.split("/")[0], "bewertung": 4.5,
         "dauer": "2-3 Stunden", "preis": 32.0, "url": f"https://foxtrail.ch/produkte/trails/{slug}/",
         "schwierigkeit": "mittel"}
    d.update(kw)
    return d


def conn_mem():
    c = db.connect(":memory:")
    db.init_db(c)
    return c


def slugs(rows):
    return sorted(r["slug"] for r in rows)


def test_typ_ableitung_und_migration():
    assert trails.typ_aus_name("Zeus Mini") == "mini"
    assert trails.typ_aus_name("Apollon MAXI") == "maxi"
    assert trails.typ_aus_name("Dominik") == "foxtrail"          # "mini" nur als ganzes Wort
    assert trails.typ_aus_name("Appenzell – Foxtrail GO", go=True) == "go"
    # Bestehende DB mit altem Typ-Schema: init_db klassifiziert Website-Trails nach,
    # manuell erfasste bleiben so, wie der Benutzer sie angelegt hat.
    c = conn_mem()
    c.executemany("INSERT INTO trails (slug, quelle, ort, name, typ, first_seen, im_angebot) "
                  "VALUES (?,?,?,?,?,'2026-01-01',?)",
                  [("x/a", "foxtrail", "O", "Apollon Maxi", "foxtrail", 1),
                   ("x/b", "foxtrail", "O", "Baccara Mini", "foxtrail", 1),
                   ("x/c", "foxtrail", "O", "Dominik", "foxtrail", 1),
                   ("manual-1", "manual", "O", "Alt Mini", "foxtrail", 0)])
    db.init_db(c)
    assert {r["slug"]: r["typ"] for r in c.execute("SELECT slug, typ FROM trails")} == {
        "x/a": "maxi", "x/b": "mini", "x/c": "foxtrail", "manual-1": "foxtrail"}


def test_neu_seit():
    c = conn_mem()
    heute = db.now()[:10]
    # Seed: neu_seit nur fuer die im Seed hinterlegten Trails (aus den Neuigkeiten), der
    # Abgleich setzt es fuer alles, was er neu anlegt
    seed_file = os.path.join(os.path.dirname(__file__), "..", "data", "trails_seed.json")
    trails.seed_from_file(c, seed_file)
    assert c.execute("SELECT COUNT(*) FROM trails WHERE neu_seit IS NOT NULL").fetchone()[0] == 8
    assert c.execute("SELECT neu_seit FROM trails WHERE slug = 'bern-und-umgebung/quarz'").fetchone()[0] == "2026-01-26"
    # bestehende DB ohne die Werte: erneutes seed fuellt nur leere neu_seit nach
    c.execute("UPDATE trails SET neu_seit = NULL")
    c.execute("UPDATE trails SET neu_seit = '2020-01-01' WHERE slug = 'aargau/veritas'")
    assert trails.seed_from_file(c, seed_file) == 0
    assert c.execute("SELECT COUNT(*) FROM trails WHERE neu_seit IS NOT NULL").fetchone()[0] == 8
    assert c.execute("SELECT neu_seit FROM trails WHERE slug = 'aargau/veritas'").fetchone()[0] == "2020-01-01"
    # dasselbe fuer die Schwierigkeit (bestehende DB vor der Spalte)
    c.execute("UPDATE trails SET schwierigkeit = NULL")
    c.execute("UPDATE trails SET schwierigkeit = 'schwierig' WHERE slug = 'aargau/aquae'")
    trails.seed_from_file(c, seed_file)
    assert c.execute("SELECT COUNT(*) FROM trails WHERE schwierigkeit IS NOT NULL").fetchone()[0] == 73
    assert c.execute("SELECT schwierigkeit FROM trails WHERE slug = 'aargau/aquae'").fetchone()[0] == "schwierig"
    seed_slugs = [r[0] for r in c.execute("SELECT slug FROM trails")]
    sync.apply(c, [mk(s) for s in seed_slugs] + [mk("jura/ganz-neu")], "test")
    t = trails.get(c, c.execute("SELECT id FROM trails WHERE slug = 'jura/ganz-neu'").fetchone()[0])
    assert t["neu_seit"] == heute and t["neu"]
    assert not trails.get(c, 1)["neu"]                  # Allalin Maxi: im Seed ohne neu_seit
    # Filter "neu": alle mit neu_seit, neueste zuerst; aeltere bleiben drin (keine Frist)
    neu = trails.list_active(c, "neu", sort="neu", richtung="desc")
    assert [x["slug"] for x in neu][:2] == ["jura/ganz-neu", "wallis/simplon"]
    assert len(neu) == 9 and neu[-1]["slug"] == "aargau/veritas"      # oben auf 2020 gesetzt
    assert trails.stats(c)["neu"] == 9


def test_migration_neu_seit():
    # Datenbank ohne die Spalte (Stand vor 2026-09): Seed-Trails teilen sich den fruehesten
    # Zeitstempel, ein spaeter per Abgleich angelegter Trail bekommt neu_seit rueckwirkend.
    c = db.connect(":memory:")
    alt = "\n".join(z for z in db.SCHEMA.splitlines()
                   if not any(k in z for k in ("neu_seit", "angelegt hat (NULL: Seed oder manuell)",
                                               "schwierigkeit")))
    c.executescript(alt)
    spalten = {r[1] for r in c.execute("PRAGMA table_info(trails)")}
    assert "neu_seit" not in spalten and "schwierigkeit" not in spalten
    c.executemany("INSERT INTO trails (slug, quelle, ort, name, first_seen) VALUES (?,?,?,?,?)",
                  [("a/1", "foxtrail", "O", "Eins", "2026-09-17 10:00:00"),
                   ("a/2", "foxtrail", "O", "Zwei", "2026-09-17 10:00:00"),
                   ("a/3", "foxtrail", "O", "Drei", "2026-10-05 04:30:00"),
                   ("manual-1", "manual", "O", "Alt", "2026-11-01 08:00:00")])
    db.init_db(c)
    assert {r[0]: r[1] for r in c.execute("SELECT slug, neu_seit FROM trails")} == {
        "a/1": None, "a/2": None, "a/3": "2026-10-05", "manual-1": None}
    db.init_db(c)                                       # zweiter Lauf aendert nichts
    assert c.execute("SELECT COUNT(*) FROM trails WHERE neu_seit IS NOT NULL").fetchone()[0] == 1
    assert "schwierigkeit" in {r[1] for r in c.execute("PRAGMA table_info(trails)")}


def test_schwierigkeit_sync():
    c = conn_mem()
    sync.apply(c, [mk("a/x", schwierigkeit="schwierig"), mk("a/go", typ="go", schwierigkeit=None)], "test")
    assert trails.get(c, 1)["schwierigkeit"] == "schwierig" and trails.get(c, 1)["grad_label"] == "Schwierig"
    assert trails.get(c, 2)["schwierigkeit"] is None and trails.get(c, 2)["grad_label"] == ""
    # None beim Abgleich = nicht ermittelt: Wert bleibt, keine "Aktualisierung"
    r = sync.apply(c, [mk("a/x", schwierigkeit=None), mk("a/go", typ="go", schwierigkeit=None)], "test")
    assert r["aktualisiert"] == 0 and trails.get(c, 1)["schwierigkeit"] == "schwierig"
    r = sync.apply(c, [mk("a/x", schwierigkeit="einfach"), mk("a/go", typ="go", schwierigkeit=None)], "test")
    assert r["aktualisiert"] == 1 and trails.get(c, 1)["schwierigkeit"] == "einfach"
    # manuell: nur gueltige Werte
    tid = trails.add_manual(c, {"ort": "O", "name": "M", "schwierigkeit": "mittel"}, "u")
    assert trails.get(c, tid)["schwierigkeit"] == "mittel"
    trails.update_manual(c, tid, {"ort": "O", "name": "M", "schwierigkeit": "unsinn"})
    assert trails.get(c, tid)["schwierigkeit"] is None


def test_dauer_parsen_und_kuerzen():
    assert trails.parse_dauer("1.5-2.5 Stunden") == (1.5, 2.5)
    assert trails.parse_dauer("2 Stunden") == (2.0, 2.0)
    assert trails.parse_dauer("") == (None, None)
    assert trails.dauer_kurz("1.5-2.5 Stunden") == "1.5–2.5 h"
    assert trails.dauer_kurz("1 Stunde") == "1 h"
    assert trails.dauer_kurz("") == ""


def test_sortieren_und_filtern():
    c = conn_mem()
    sync.apply(c, [mk("aargau/aquae", ort="Baden", name="Aquae", preis=32.0, bewertung=4.3,
                      dauer="2.5-3.5 Stunden", schwierigkeit="mittel"),
                   mk("wallis/simplon", ort="Brig", name="Simplon", preis=36.0, bewertung=4.6,
                      dauer="3-4 Stunden", schwierigkeit="schwierig"),
                   mk("ostschweiz/baccara-mini", ort="Rapperswil", name="Baccara Mini", typ="mini",
                      preis=19.0, bewertung=None, dauer="1-2 Stunden", schwierigkeit="einfach")], "test")

    def names(**kw):
        return [t["name"] for t in trails.list_active(c, **kw)]

    assert names() == ["Aquae", "Simplon", "Baccara Mini"]                  # Standard: Ort
    assert names(sort="preis") == ["Baccara Mini", "Aquae", "Simplon"]
    assert names(sort="preis", richtung="desc") == ["Simplon", "Aquae", "Baccara Mini"]
    assert names(sort="bewertung", richtung="desc") == ["Simplon", "Aquae", "Baccara Mini"]  # ohne Wert am Ende
    assert names(sort="bewertung") == ["Aquae", "Simplon", "Baccara Mini"]
    assert names(sort="dauer") == ["Baccara Mini", "Aquae", "Simplon"]
    assert names(sort="typ") == ["Aquae", "Simplon", "Baccara Mini"]
    assert names(sort="region") == ["Aquae", "Baccara Mini", "Simplon"]      # Aargau, Ostschweiz, Wallis
    assert names(typ=["mini"]) == ["Baccara Mini"]
    assert names(typ="mini") == ["Baccara Mini"]
    assert names(region=["aargau", "wallis"]) == ["Aquae", "Simplon"]
    assert names(dauer=["1-2 Stunden"]) == ["Baccara Mini"]
    assert names(typ=["unsinn"]) == ["Aquae", "Simplon", "Baccara Mini"]     # unbekannt = ignoriert
    assert names(sort="schwierigkeit") == ["Baccara Mini", "Aquae", "Simplon"]
    assert names(sort="schwierigkeit", richtung="desc") == ["Simplon", "Aquae", "Baccara Mini"]
    assert names(grad=["mittel", "schwierig"]) == ["Aquae", "Simplon"]
    o = trails.filter_options(c)
    assert o["region"] == [("aargau", "Aargau"), ("ostschweiz", "Ostschweiz"), ("wallis", "Wallis")]
    assert [d for d, _ in o["dauer"]] == ["1-2 Stunden", "2.5-3.5 Stunden", "3-4 Stunden"]
    assert o["dauer"][0][1] == "1–2 h"


def test_neu_und_update():
    c = conn_mem()
    r = sync.apply(c, [mk("aargau/aquae"), mk("wallis/simplon")], "test")
    assert r["ok"] and r["neu"] == 2
    assert trails.stats(c)["total"] == 2
    # Preisaenderung -> aktualisiert, Eintraege bleiben
    trails.update_done(c, 1, {"gemacht": "1", "gemacht_datum": "2025-06-01",
                              "mitspieler": "4", "bemerkung": "super"}, "bruno")
    r = sync.apply(c, [mk("aargau/aquae", preis=36.0), mk("wallis/simplon")], "test")
    assert r["neu"] == 0 and r["aktualisiert"] == 1
    t = trails.get(c, 1)
    assert t["preis"] == 36.0 and t["gemacht"] == 1 and t["mitspieler"] == 4
    assert t["gemacht_datum"] == "2025-06-01" and t["bemerkung"] == "super"


def test_archiv_regeln():
    c = conn_mem()
    sync.apply(c, [mk("a/gemacht"), mk("a/offen"), mk("a/bleibt")], "test")
    trails.update_done(c, 1, {"gemacht": "1", "gemacht_datum": "2024-01-01"}, "u")
    # beide verschwinden von der Website
    r = sync.apply(c, [mk("a/bleibt"), mk("a/bleibt2")], "test")
    assert r["ok"]
    assert r["nicht_mehr_im_angebot"] == 1 and r["archiviert"] == 1 and r["neu"] == 1
    haupt = trails.list_active(c)
    assert slugs(haupt) == ["a/bleibt", "a/bleibt2", "a/gemacht"]      # gemacht nie geloescht
    gem = next(t for t in haupt if t["slug"] == "a/gemacht")
    assert gem["im_angebot"] == 0 and not gem["archiviert"]
    assert slugs(trails.list_archiv(c)) == ["a/offen"]
    assert c.execute("SELECT COUNT(*) FROM trails").fetchone()[0] == 4  # nichts geloescht


def test_archiv_gemacht_kehrt_zurueck():
    c = conn_mem()
    sync.apply(c, [mk("a/x")], "test")
    sync.apply(c, [mk("a/y")], "test")          # x ins Archiv (offen + weg)
    assert slugs(trails.list_archiv(c)) == ["a/x"]
    # im Archiv als gemacht markiert -> zurueck in die Hauptliste
    trails.update_done(c, 1, {"gemacht": "1"}, "u")
    assert trails.list_archiv(c) == []
    assert "a/x" in slugs(trails.list_active(c))


def test_reaktivierung():
    c = conn_mem()
    sync.apply(c, [mk("a/x")], "test")
    sync.apply(c, [mk("a/y")], "test")
    r = sync.apply(c, [mk("a/x"), mk("a/y")], "test")
    assert r["reaktiviert"] == 1
    assert trails.list_archiv(c) == []
    assert trails.get(c, 1)["im_angebot"] == 1


def test_manuelle_trails_unberuehrt():
    c = conn_mem()
    sync.apply(c, [mk("a/x")], "test")
    mid = trails.add_manual(c, {"ort": "Altdorf", "name": "Tell", "gemacht": "1",
                                "gemacht_datum": "2019-05-05", "mitspieler": "3"}, "u")
    r = sync.apply(c, [mk("a/x")], "test")
    assert r["archiviert"] == 0 and r["nicht_mehr_im_angebot"] == 0
    m = trails.get(c, mid)
    assert m["quelle"] == "manual" and not m["archiviert"] and m["gemacht"] == 1
    # manueller Trail, noch offen: landet trotzdem nicht im Archiv
    mid2 = trails.add_manual(c, {"ort": "Altdorf", "name": "Offen"}, "u")
    sync.apply(c, [mk("a/x")], "test")
    assert not trails.get(c, mid2)["archiviert"]


def test_sicherung_bei_zu_wenig_treffern():
    c = conn_mem()
    sync.apply(c, [mk(f"a/t{i}") for i in range(10)], "test")
    r = sync.apply(c, [mk("a/t1"), mk("a/t2")], "test")      # nur 20 % -> Abbruch
    assert not r["ok"] and "abgebrochen" in r["meldung"]
    assert trails.stats(c)["archiv"] == 0
    r = sync.apply(c, [], "test")
    assert not r["ok"]
    assert len(sync.last_runs(c)) == 3


def test_seed_insert_only(tmp_path):
    import json
    c = conn_mem()
    p = tmp_path / "seed.json"
    p.write_text(json.dumps({"trails": [mk("a/x"), mk("a/y")]}), encoding="utf-8")
    assert trails.seed_from_file(c, str(p)) == 2
    sync.apply(c, [mk("a/x"), mk("a/z")], "test")          # y weg, z neu
    assert trails.seed_from_file(c, str(p)) == 0            # y nicht neu eingefuegt
    assert trails.get(c, 3)["im_angebot"] == 1               # z bleibt im Angebot
    assert slugs(trails.list_archiv(c)) == ["a/y"]
