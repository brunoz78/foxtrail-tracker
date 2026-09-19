# -*- coding: utf-8 -*-
"""Import der Bestellungen von foxtrail.ch (anonymisiertes Muster der Kontoseite)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxtrail import bestellungen, sync, trails  # noqa: E402
from tests.test_sync import conn_mem, mk  # noqa: E402

MUSTER = """Max Muster

(max@example.org)

Deine Bestellungen
Bestellung vom 17.09.2026 (#418324)

Status: Abgeschlossen

Buchung
Team 1
Trail Columban

Team code: AAAAAA

Startzeit: 17.09.2026 12:00

Erwachsene: 2

Sprache: Deutsch

Startunterlagen Download
Schlussfoto Download
Rechnung #418324 als PDF
Bestellung vom 24.05.2026 (#388553)

Status: Abgeschlossen

Buchung
Team 1
Trail Hera

Team code: BBBBBB

Startzeit: 25.05.2026 09:45

Erwachsene: 2

Kinder: 1

Sprache: Deutsch
Team 2
Trail Hera

Team code: CCCCCC

Startzeit: 25.05.2026 09:45

Erwachsene: 3

Sprache: Deutsch

Rechnung #388553 als PDF
Bestellung vom 01.02.2026 (#300000)

Status: Storniert

Buchung
Team 1
Trail Granit

Team code: DDDDDD

Startzeit: 02.02.2026 10:00

Erwachsene: 2
Bestellung vom 17.03.2024 (#102858)

Status: Abgeschlossen

Buchung
Team 1
Trail Galileo

Team code: EEEEEE

Startzeit: 19.03.2024 10:00

Erwachsene: 2
Bestellung vom 30.12.2026 (#500000)

Status: Abgeschlossen

Buchung
Team 1
Trail Yellow

Team code: FFFFFF

Startzeit: 31.12.2026 10:00

Erwachsene: 2
"""

MUSTER_JSON = {
    "user": {"first_name": "Max"},
    "orders": [
        {"id": 418324, "date_created": "2026-09-17T08:09:51.268Z", "status": "completed",
         "booking": {"teams": [
             {"code": "AAAAAA", "trail": {"id": 28, "name": "Columban"},
              "participants_booked": [{"quantity": 2, "segment": "adult"}, {"quantity": 0, "segment": "child"}],
              "start_time_actual": "2026-09-17T12:02:06.000Z", "finish_time": "2026-09-17T14:52:08.000Z",
              "confirmed_adult_tickets": 2, "confirmed_child_tickets": 0,
              "img_public_url": "https://storage.example.org/fox-camera/AAAAAA/1.jpg",
              "slot": {"date_time": "2026-09-17T12:00:00.000Z"}}]},
         "invoice": "https://api.foxtrail.ch/invoice/print/418324"},
        {"id": 388553, "date_created": "2026-05-24T16:45:04.885Z", "status": "completed",
         "booking": {"teams": [
             {"code": "BBBBBB", "trail": {"name": "Hera"}, "confirmed_adult_tickets": 2, "confirmed_child_tickets": 1,
              "start_time_actual": "2026-05-25T09:50:00.000Z", "finish_time": "2026-05-25T12:10:00.000Z",
              "slot": {"date_time": "2026-05-25T09:45:00.000Z"}},
             {"code": "CCCCCC", "trail": {"name": "Hera"}, "confirmed_adult_tickets": 0, "confirmed_child_tickets": 0,
              "participants_booked": [{"quantity": 3, "segment": "adult"}],
              "start_time_actual": "2026-05-25T09:40:00.000Z", "finish_time": "2026-05-25T12:30:00.000Z",
              "img_public_url": "https://storage.example.org/fox-camera/CCCCCC/2.jpg",
              "slot": {"date_time": "2026-05-25T09:45:00.000Z"}}]}},
        {"id": 300000, "date_created": "2026-02-01T10:00:00.000Z", "status": "cancelled",
         "booking": {"teams": [{"trail": {"name": "Granit"}, "confirmed_adult_tickets": 2,
                                "slot": {"date_time": "2026-02-02T10:00:00.000Z"}}]}},
        {"id": 1, "date_created": "2026-01-01T10:00:00.000Z", "status": "completed",
         "booking": {"teams": []}, "ordered_vouchers": [{"id": 5}]},
    ],
}


def test_parse_text():
    e = bestellungen.parse(MUSTER)
    assert [(x["name"], x["datum"], x["personen"], x["teams"]) for x in e] == [
        ("Columban", "2026-09-17", 2, 1),
        ("Hera", "2026-05-25", 6, 2),          # 2 Teams: 2+1 und 3 Personen
        ("Granit", "2026-02-02", 2, 1),
        ("Galileo", "2024-03-19", 2, 1),
        ("Yellow", "2026-12-31", 2, 1),
    ]
    assert e[0]["bestellung"] == "418324" and e[0]["bestellt_am"] == "2026-09-17"
    assert e[0]["codes"] == ["AAAAAA"] and e[1]["codes"] == ["BBBBBB", "CCCCCC"]
    assert e[0]["start"] is None and e[0]["foto_url"] is None      # nur im JSON
    assert e[2]["status"] == "Storniert"


def test_parse_html():
    html = "<html><body><div><h2>Bestellung vom 17.09.2026 (#1)</h2><p>Status: <span>Abgeschlossen</span></p>" \
           "<h4>Team 1</h4><h3>Trail Columban</h3><p>Team code: <b>X1</b></p><p>Startzeit: 17.09.2026 12:00</p>" \
           "<p>Erwachsene: 2</p></div></body></html>"
    e = bestellungen.parse(html)
    assert len(e) == 1 and e[0]["name"] == "Columban" and e[0]["datum"] == "2026-09-17" and e[0]["personen"] == 2
    assert e[0]["codes"] == ["X1"]
    assert bestellungen.parse("") == []


def test_parse_json():
    e = bestellungen.parse(json.dumps(MUSTER_JSON))
    assert [(x["name"], x["datum"], x["personen"], x["teams"], x["status"]) for x in e] == [
        ("Columban", "2026-09-17", 2, 1, "Abgeschlossen"),
        ("Hera", "2026-05-25", 6, 2, "Abgeschlossen"),
        ("Granit", "2026-02-02", 2, 1, "Storniert"),
    ]
    assert e[0]["bestellung"] == "418324" and e[0]["bestellt_am"] == "2026-09-17"
    assert e[0]["start"] == "2026-09-17 12:02" and e[0]["ziel"] == "2026-09-17 14:52"
    assert e[0]["codes"] == ["AAAAAA"] and e[0]["foto_url"].endswith("/AAAAAA/1.jpg")
    # zwei Teams: fruehester Start, spaetestes Ziel, erstes Foto
    assert e[1]["start"] == "2026-05-25 09:40" and e[1]["ziel"] == "2026-05-25 12:30"
    assert e[1]["codes"] == ["BBBBBB", "CCCCCC"] and e[1]["foto_url"].endswith("/CCCCCC/2.jpg")
    assert e[2]["start"] is None and e[2]["foto_url"] is None
    assert bestellungen.parse("{kein json") == []            # faellt auf Text zurueck: nichts drin
    assert bestellungen.parse(json.dumps({"orders": []})) == []


def test_eintraege_aus_json_roundtrip():
    e = bestellungen.parse(json.dumps(MUSTER_JSON))
    zurueck = bestellungen.eintraege_aus_json(json.dumps(e))
    assert zurueck == e
    import pytest
    with pytest.raises(ValueError):
        bestellungen.eintraege_aus_json(json.dumps([{"datum": "2026-01-01"}]))   # ohne Name
    with pytest.raises(ValueError):
        bestellungen.eintraege_aus_json(json.dumps({"name": "x"}))                # keine Liste
    assert bestellungen.eintraege_aus_json(json.dumps([{"name": "A", "foto_url": "http://unsicher"}]))[0]["foto_url"] is None


class _Antwort:
    def __init__(self, content=b"\xff\xd8bild", ct="image/jpeg", status=200):
        self.content, self.headers, self.status_code = content, {"Content-Type": ct}, status

    def raise_for_status(self):
        if self.status_code != 200:
            raise bestellungen.requests.HTTPError("kaputt")


def _db():
    c = conn_mem()
    sync.apply(c, [mk("ostschweiz/columban", ort="St. Gallen", name="Columban"),
                   mk("ostschweiz/columban-mini", ort="St. Gallen", name="Columban Mini", typ="mini"),
                   mk("zuerich/hera", ort="Zürich", name="Hera"),
                   mk("bern/granit", ort="Bern", name="Granit"),
                   mk("basel/yellow", ort="Basel", name="Yellow")], "test")
    return c


def _id(c, slug):
    return c.execute("SELECT id FROM trails WHERE slug = ?", (slug,)).fetchone()[0]


def test_zuordnen_und_anwenden_text():
    c = _db()
    hera = _id(c, "zuerich/hera")
    trails.update_done(c, hera, {"gemacht": "1", "gemacht_datum": "2020-01-01", "bemerkung": "alt"}, "u")
    plan = bestellungen.zuordnen(c, bestellungen.parse(MUSTER), heute="2026-09-17")
    aktionen = {e["name"]: (t["slug"] if t else None, a, g) for e, t, a, g in plan}
    assert aktionen["Columban"] == ("ostschweiz/columban", "setzen", "")          # nicht Columban Mini
    assert aktionen["Hera"][1] == "ergaenzen"                                      # gemacht, Code fehlt noch
    assert aktionen["Granit"] == ("bern/granit", "uebersprungen", "Status Storniert")
    assert aktionen["Galileo"] == (None, "unbekannt", "kein Trail mit diesem Namen in der Liste")
    assert aktionen["Yellow"][1] == "uebersprungen" and "Zukunft" in aktionen["Yellow"][2]
    res = bestellungen.anwenden(c, plan, "bruno")
    assert res == {"gesetzt": 1, "ergaenzt": 1, "fotos": 0, "foto_fehler": 0}
    col = trails.get(c, _id(c, "ostschweiz/columban"))
    assert col["gemacht"] == 1 and col["gemacht_datum"] == "2026-09-17" and col["mitspieler"] == 2
    assert col["erfasst_von"] == "bruno" and col["team_code"] == "AAAAAA" and col["bestellung"] == "418324"
    h = trails.get(c, hera)
    datum_bestellung = next(e["datum"] for e, _, _, _ in plan if e["name"] == "Hera")
    assert "Datum 01.01.2020 → " in aktionen["Hera"][2] and "Team-Code" in aktionen["Hera"][2]
    assert h["gemacht_datum"] == datum_bestellung and h["bemerkung"] == "alt"   # Datum korrigiert
    assert h["team_code"] == "BBBBBB, CCCCCC"                                    # ergaenzt
    # zweiter Lauf: alles komplett -> nichts mehr zu tun
    plan = bestellungen.zuordnen(c, bestellungen.parse(MUSTER), heute="2026-09-17")
    assert bestellungen.anwenden(c, plan)["gesetzt"] == 0
    assert {a for _, _, a, _ in plan if _ or True} <= {"uebersprungen", "unbekannt"}


def test_anwenden_json_mit_fotos(tmp_path, monkeypatch):
    c = _db()
    aufrufe = []

    def fake_get(url, timeout=None, headers=None):
        aufrufe.append(url)
        if "CCCCCC" in url:
            return _Antwort(status=500)
        return _Antwort()

    monkeypatch.setattr(bestellungen.requests, "get", fake_get)
    plan = bestellungen.zuordnen(c, bestellungen.parse(json.dumps(MUSTER_JSON)), heute="2026-09-17")
    res = bestellungen.anwenden(c, plan, "bruno", foto_dir=str(tmp_path / "fotos"))
    assert res == {"gesetzt": 2, "ergaenzt": 0, "fotos": 1, "foto_fehler": 1}
    col = trails.get(c, _id(c, "ostschweiz/columban"))
    assert col["start_zeit"] == "2026-09-17 12:02" and col["ziel_zeit"] == "2026-09-17 14:52"
    assert col["spielzeit_min"] == 170 and col["spielzeit"] == "2:50 h"
    assert col["foto"] == f"{col['id']}.jpg" and (tmp_path / "fotos" / col["foto"]).read_bytes() == b"\xff\xd8bild"
    assert col["foto_url"].endswith("/AAAAAA/1.jpg")
    hera = trails.get(c, _id(c, "zuerich/hera"))
    assert hera["foto"] is None and hera["foto_url"].endswith("/CCCCCC/2.jpg") and hera["mitspieler"] == 6
    assert len(aufrufe) == 2
    # erneuter Lauf: Foto fuer Hera wird nachgeholt (fehlt noch), Columban bleibt unangetastet
    monkeypatch.setattr(bestellungen.requests, "get", lambda url, timeout=None, headers=None: _Antwort())
    plan = bestellungen.zuordnen(c, bestellungen.parse(json.dumps(MUSTER_JSON)), heute="2026-09-17")
    assert [a for _, t, a, _ in plan if t and t["name"] == "Hera"] == ["ergaenzen"]
    res = bestellungen.anwenden(c, plan, "bruno", foto_dir=str(tmp_path / "fotos"))
    assert res["ergaenzt"] == 1 and res["fotos"] == 1
    assert trails.get(c, _id(c, "zuerich/hera"))["foto"] is not None



def test_ergaenzen_datum_und_geloeschtes_foto():
    c = _db()
    hera = _id(c, "zuerich/hera")
    trails.update_done(c, hera, {"gemacht": "1", "gemacht_datum": "2020-01-01"}, "u")
    trails.set_foto(c, hera, "")                                      # von Hand geloescht
    eintraege = bestellungen.parse(json.dumps(MUSTER_JSON))
    plan = bestellungen.zuordnen(c, eintraege, heute="2026-09-17")
    grund = next(g for e, t, a, g in plan if t and t["id"] == hera)
    assert "von Hand gelöscht" in grund and "Schlussfoto," not in grund
    # zwei Bestellungen fuer denselben Trail: Datum bleibt, weil unklar ist, welche gemeint ist
    doppelt = eintraege + [dict(e, datum="2019-05-05") for e in eintraege if e["name"] == "Hera"]
    plan = bestellungen.zuordnen(c, doppelt, heute="2026-09-17")
    assert all("Datum " not in g for e, t, a, g in plan if t and t["id"] == hera)



def test_hinweis_foto_ersetzt():
    c = _db()
    hera = _id(c, "zuerich/hera")
    eintraege = bestellungen.parse(json.dumps(MUSTER_JSON))

    def grund():
        return next(g for e, t, a, g in bestellungen.zuordnen(c, eintraege, heute="2026-09-17")
                    if t and t["id"] == hera)

    trails.set_foto(c, hera, f"{hera}-1789822635.jpg")           # eigenes Foto, noch offen
    assert "durch ein anderes Foto ersetzt" in grund()
    trails.update_done(c, hera, {"gemacht": "1", "gemacht_datum": "2020-01-01"}, "u")
    assert "durch ein anderes Foto ersetzt" in grund()           # gemacht: beim Ergaenzen ebenso
    trails.set_foto(c, hera, f"{hera}.jpg")                      # Schlussfoto liegt schon da
    assert "Foto" not in grund()


def test_spielzeit():
    assert trails.spielzeit_min("2026-09-17 12:02", "2026-09-17 14:52") == 170
    assert trails.spielzeit_min("2026-09-17 23:30", "2026-09-18 01:00") == 90
    assert trails.spielzeit_min("2026-09-17 12:02", None) is None
    assert trails.spielzeit_min("2026-09-17 12:02", "2026-09-17 11:00") is None
    assert trails.spielzeit_label(170) == "2:50 h" and trails.spielzeit_label(None) == ""
