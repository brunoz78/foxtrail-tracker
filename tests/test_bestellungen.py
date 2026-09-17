# -*- coding: utf-8 -*-
"""Import der Bestellungen von foxtrail.ch (anonymisiertes Muster der Kontoseite)."""

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
    assert e[2]["status"] == "Storniert"


def test_parse_html():
    html = "<html><body><div><h2>Bestellung vom 17.09.2026 (#1)</h2><p>Status: <span>Abgeschlossen</span></p>" \
           "<h4>Team 1</h4><h3>Trail Columban</h3><p>Team code: <b>X</b></p><p>Startzeit: 17.09.2026 12:00</p>" \
           "<p>Erwachsene: 2</p></div></body></html>"
    e = bestellungen.parse(html)
    assert len(e) == 1 and e[0]["name"] == "Columban" and e[0]["datum"] == "2026-09-17" and e[0]["personen"] == 2
    assert bestellungen.parse("") == []


def test_zuordnen_und_anwenden():
    c = conn_mem()
    sync.apply(c, [mk("ostschweiz/columban", ort="St. Gallen", name="Columban"),
                   mk("ostschweiz/columban-mini", ort="St. Gallen", name="Columban Mini", typ="mini"),
                   mk("zuerich/hera", ort="Zürich", name="Hera"),
                   mk("bern/granit", ort="Bern", name="Granit"),
                   mk("basel/yellow", ort="Basel", name="Yellow")], "test")
    hera = c.execute("SELECT id FROM trails WHERE slug = 'zuerich/hera'").fetchone()[0]
    trails.update_done(c, hera, {"gemacht": "1", "gemacht_datum": "2020-01-01", "bemerkung": "alt"}, "u")
    plan = bestellungen.zuordnen(c, bestellungen.parse(MUSTER), heute="2026-09-17")
    aktionen = {e["name"]: (t["slug"] if t else None, a, g) for e, t, a, g in plan}
    assert aktionen["Columban"] == ("ostschweiz/columban", "setzen", "")          # nicht Columban Mini
    assert aktionen["Hera"][1] == "uebersprungen" and "bereits" in aktionen["Hera"][2]
    assert aktionen["Granit"] == ("bern/granit", "uebersprungen", "Status Storniert")
    assert aktionen["Galileo"] == (None, "unbekannt", "kein Trail mit diesem Namen in der Liste")
    assert aktionen["Yellow"][1] == "uebersprungen" and "Zukunft" in aktionen["Yellow"][2]
    assert bestellungen.anwenden(c, plan, "bruno") == 1
    col = trails.get(c, c.execute("SELECT id FROM trails WHERE slug = 'ostschweiz/columban'").fetchone()[0])
    assert col["gemacht"] == 1 and col["gemacht_datum"] == "2026-09-17" and col["mitspieler"] == 2
    assert col["erfasst_von"] == "bruno"
    assert trails.get(c, hera)["gemacht_datum"] == "2020-01-01" and trails.get(c, hera)["bemerkung"] == "alt"
    # zweiter Lauf: Columban jetzt "bereits gemacht", nichts mehr zu tun
    plan = bestellungen.zuordnen(c, bestellungen.parse(MUSTER), heute="2026-09-17")
    assert bestellungen.anwenden(c, plan) == 0
