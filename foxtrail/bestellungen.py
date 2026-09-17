# -*- coding: utf-8 -*-
"""
Import der eigenen Bestellungen von foxtrail.ch ("Account -> Deine Bestellungen").

Drei Eingabeformen, parse() erkennt sie selbst:

1. JSON der Kontodaten - die Seite holt sie von
   https://foxtrail.ch/wp-json/foxtrail/v1/proxy/account (angemeldet im Browser
   aufrufen und speichern). Je Bestellung: status ('completed' | ...), booking.teams[]
   mit trail.name, slot.date_time (gebuchter Start, Ortszeit mit "Z"-Suffix),
   start_time_actual, confirmed_adult_tickets / confirmed_child_tickets bzw.
   participants_booked[]. Das ist die verlaesslichste Quelle.
2. Die Seite als "Webseite, vollstaendig" gespeichert (HTML).
3. Der kopierte Seitentext. HTML und Text sehen je Bestellung so aus:

    Bestellung vom 17.09.2026 (#418324)
    Status: Abgeschlossen
    Buchung
    Team 1
    Trail Columban
    Team code: NQEDNG
    Startzeit: 17.09.2026 12:00
    Erwachsene: 2
    Kinder: 1            (nur wenn vorhanden)
    Sprache: Deutsch
    ...
    Team 2               (bei mehreren Teams je Bestellung)
    ...

parse() liest daraus je Team einen Eintrag (Trail, Datum, Personen), fasst Teams
derselben Bestellung und desselben Trails zusammen und liefert eine Liste. Das
Anwenden auf die DB ordnet ueber den exakten Trail-Namen zu ("Trail Columban" ->
"Columban", nicht "Columban Mini") und setzt gemacht/Datum/Mitspieler - nie bei
bereits gemachten Trails, nie bei noch nicht gespielten (Startzeit in der Zukunft).
"""

import datetime
import json
import re

from bs4 import BeautifulSoup

from . import trails

_ORDER_RE = re.compile(r"Bestellung vom (\d{2}\.\d{2}\.\d{4}) \(#(\d+)\)")
_TEAM_RE = re.compile(r"^Team \d+\s*$", re.M)
_TRAIL_RE = re.compile(r"^Trail (.+?)\s*$", re.M)
_START_RE = re.compile(r"Startzeit:\s*(\d{2})\.(\d{2})\.(\d{4})")
_ERW_RE = re.compile(r"Erwachsene:\s*(\d+)")
_KIND_RE = re.compile(r"Kinder:\s*(\d+)")
_STATUS_RE = re.compile(r"Status:\s*(\S+)")


def _text(inhalt):
    """HTML oder Text -> reiner Text mit Zeilenumbruechen."""
    if "<" in inhalt and re.search(r"<(html|div|body|p)\b", inhalt, re.I):
        return BeautifulSoup(inhalt, "html.parser").get_text("\n")
    return inhalt


def _iso(t, m, j):
    return f"{j}-{m}-{t}"


_STATUS_LABEL = {"completed": "Abgeschlossen", "cancelled": "Storniert", "canceled": "Storniert",
                 "refunded": "Rueckerstattet", "pending": "Offen", "processing": "In Bearbeitung"}


def _parse_json(daten):
    """Kontodaten-JSON (siehe Modul-Doku) -> dieselbe Eintragsliste wie beim Text."""
    orders = daten.get("orders", []) if isinstance(daten, dict) else daten
    out = []
    for o in orders or []:
        status = str(o.get("status") or "")
        status = _STATUS_LABEL.get(status.lower(), status)
        je_trail = {}
        for team in ((o.get("booking") or {}).get("teams") or []):
            name = re.sub(r"\s+", " ", str((team.get("trail") or {}).get("name") or "")).strip()
            if not name:
                continue
            start = (team.get("slot") or {}).get("date_time") or team.get("start_time_actual") or ""
            datum = start[:10] if re.match(r"\d{4}-\d{2}-\d{2}", start) else None
            personen = (team.get("confirmed_adult_tickets") or 0) + (team.get("confirmed_child_tickets") or 0)
            if not personen:
                personen = sum(int(p.get("quantity") or 0) for p in (team.get("participants_booked") or []))
            e = je_trail.setdefault((name, datum), {"name": name, "datum": datum, "personen": 0, "teams": 0})
            e["personen"] += personen
            e["teams"] += 1
        for e in je_trail.values():
            e.update({"bestellung": str(o.get("id") or ""), "bestellt_am": str(o.get("date_created") or "")[:10],
                      "status": status})
            out.append(e)
    return out


def parse(inhalt):
    """Liste von Eintraegen: {bestellung, bestellt_am, status, name, datum, personen, teams}."""
    kopf = inhalt.lstrip()[:1]
    if kopf in ("{", "["):
        try:
            return _parse_json(json.loads(inhalt))
        except ValueError:
            pass                                   # kein JSON -> als Text versuchen
    text = _text(inhalt)
    hits = list(_ORDER_RE.finditer(text))
    out = []
    for i, h in enumerate(hits):
        block = text[h.end():hits[i + 1].start() if i + 1 < len(hits) else len(text)]
        sm = _STATUS_RE.search(block)
        status = sm.group(1).strip() if sm else ""
        teile = _TEAM_RE.split(block)
        teams = teile[1:] if len(teile) > 1 else [block]
        je_trail = {}
        for seg in teams:
            tm, dm, em = _TRAIL_RE.search(seg), _START_RE.search(seg), _ERW_RE.search(seg)
            if not tm:
                continue
            name = re.sub(r"\s+", " ", tm.group(1)).strip()
            datum = _iso(dm.group(1), dm.group(2), dm.group(3)) if dm else None
            personen = int(em.group(1)) if em else 0
            km = _KIND_RE.search(seg)
            if km:
                personen += int(km.group(1))
            key = (name, datum)
            e = je_trail.setdefault(key, {"name": name, "datum": datum, "personen": 0, "teams": 0})
            e["personen"] += personen
            e["teams"] += 1
        for e in je_trail.values():
            e.update({"bestellung": h.group(2), "bestellt_am": _iso(*h.group(1).split(".")),
                      "status": status})
            out.append(e)
    return out


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def zuordnen(conn, eintraege, heute=None):
    """Jeden Eintrag einem Trail zuordnen und entscheiden, was passieren wuerde.
    Ergibt Liste von (eintrag, trail_oder_None, aktion, grund) mit aktion in
    'setzen' | 'uebersprungen' | 'unbekannt'."""
    heute = heute or datetime.date.today().isoformat()
    alle = [dict(r) for r in conn.execute("SELECT * FROM trails")]
    by_name = {}
    for t in alle:
        by_name.setdefault(_norm(t["name"]), []).append(t)
    plan = []
    for e in eintraege:
        kand = by_name.get(_norm(e["name"]), [])
        # bei mehreren gleichnamigen (foxtrail + manuell) den Website-Trail bevorzugen
        kand.sort(key=lambda t: (t["quelle"] != "foxtrail", t["id"]))
        t = kand[0] if kand else None
        if t is None:
            plan.append((e, None, "unbekannt", "kein Trail mit diesem Namen in der Liste"))
        elif e["status"] and e["status"].lower() != "abgeschlossen":
            plan.append((e, t, "uebersprungen", f"Status {e['status']}"))
        elif not e["datum"]:
            plan.append((e, t, "uebersprungen", "keine Startzeit"))
        elif e["datum"] > heute:
            plan.append((e, t, "uebersprungen", "Startzeit liegt in der Zukunft"))
        elif t["gemacht"]:
            plan.append((e, t, "uebersprungen",
                         f"bereits als gemacht erfasst ({t['gemacht_datum'] or 'ohne Datum'})"))
        else:
            plan.append((e, t, "setzen", ""))
    return plan


def anwenden(conn, plan, benutzer="import"):
    """Nur die 'setzen'-Zeilen schreiben; Bemerkung bleibt erhalten. Gibt Anzahl zurueck."""
    n = 0
    for e, t, aktion, _ in plan:
        if aktion != "setzen":
            continue
        trails.update_done(conn, t["id"], {
            "gemacht": "1", "gemacht_datum": e["datum"],
            "mitspieler": str(e["personen"]) if e["personen"] else "",
            "bemerkung": t["bemerkung"]}, benutzer)
        n += 1
    return n
