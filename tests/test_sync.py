# -*- coding: utf-8 -*-
"""Tests der Abgleich-Regeln (ohne Netz, In-Memory-SQLite)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxtrail import db, sync, trails  # noqa: E402


def mk(slug, **kw):
    d = {"slug": slug, "ort": "Ort", "name": slug.split("/")[-1].title(), "route": "A - B",
         "typ": "foxtrail", "region": slug.split("/")[0], "bewertung": 4.5,
         "dauer": "2-3 Stunden", "preis": 32.0, "url": f"https://foxtrail.ch/produkte/trails/{slug}/"}
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
                      dauer="2.5-3.5 Stunden"),
                   mk("wallis/simplon", ort="Brig", name="Simplon", preis=36.0, bewertung=4.6,
                      dauer="3-4 Stunden"),
                   mk("ostschweiz/baccara-mini", ort="Rapperswil", name="Baccara Mini", typ="mini",
                      preis=19.0, bewertung=None, dauer="1-2 Stunden")], "test")

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
