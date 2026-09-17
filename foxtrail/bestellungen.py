# -*- coding: utf-8 -*-
"""
Import der eigenen Bestellungen von foxtrail.ch ("Account -> Deine Bestellungen").

Drei Eingabeformen, parse() erkennt sie selbst:

1. JSON der Kontodaten - die Seite holt sie von
   https://foxtrail.ch/wp-json/foxtrail/v1/proxy/account (angemeldet im Browser
   aufrufen und speichern). Je Bestellung: status ('completed' | ...), booking.teams[]
   mit trail.name, slot.date_time (gebuchter Start, Ortszeit mit "Z"-Suffix),
   start_time_actual / finish_time (tatsaechlich), confirmed_adult_tickets /
   confirmed_child_tickets bzw. participants_booked[], code (Team-Code) und
   img_public_url (Schlussfoto, oeffentlich). Das ist die verlaesslichste Quelle.
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

   Zeiten und Foto liefert nur das JSON.

parse() liest daraus je Team einen Eintrag, fasst Teams derselben Bestellung und
desselben Trails zusammen (Personen addiert, fruehester Start, spaetestes Ziel) und
liefert eine Liste. zuordnen() ordnet ueber den exakten Trail-Namen zu ("Trail
Columban" -> "Columban", nicht "Columban Mini") und plant:
  setzen        offener Trail -> gemacht, Datum, Mitspieler + Zusatzdaten
  ergaenzen     bereits gemacht, aber Zeiten/Foto/Code fehlen -> nur diese nachtragen
  uebersprungen storniert, ohne Startzeit, in der Zukunft oder schon komplett
  unbekannt     kein Trail mit diesem Namen
anwenden() schreibt nur setzen/ergaenzen, laesst Bemerkungen stehen und laedt das
Schlussfoto einmalig nach config.foto_dir() (oeffentliche URL, kein Login noetig).
"""

import datetime
import json
import os
import re

import requests
from bs4 import BeautifulSoup

from . import config, trails

_ORDER_RE = re.compile(r"Bestellung vom (\d{2}\.\d{2}\.\d{4}) \(#(\d+)\)")
_TEAM_RE = re.compile(r"^Team \d+\s*$", re.M)
_TRAIL_RE = re.compile(r"^Trail (.+?)\s*$", re.M)
_START_RE = re.compile(r"Startzeit:\s*(\d{2})\.(\d{2})\.(\d{4})")
_ERW_RE = re.compile(r"Erwachsene:\s*(\d+)")
_KIND_RE = re.compile(r"Kinder:\s*(\d+)")
_CODE_RE = re.compile(r"Team code:\s*([A-Z0-9]+)")
_STATUS_RE = re.compile(r"Status:\s*(\S+)")
_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})")
_STATUS_LABEL = {"completed": "Abgeschlossen", "cancelled": "Storniert", "canceled": "Storniert",
                 "refunded": "Rueckerstattet", "pending": "Offen", "processing": "In Bearbeitung"}
FELDER = ("name", "datum", "personen", "teams", "bestellung", "bestellt_am", "status",
          "start", "ziel", "codes", "foto_url")
MAX_FOTO_BYTES = 8 * 1024 * 1024


def _text(inhalt):
    """HTML oder Text -> reiner Text mit Zeilenumbruechen."""
    if "<" in inhalt and re.search(r"<(html|div|body|p)\b", inhalt, re.I):
        return BeautifulSoup(inhalt, "html.parser").get_text("\n")
    return inhalt


def _iso(t, m, j):
    return f"{j}-{m}-{t}"


def _zeit(s):
    """'2026-09-17T12:02:06.000Z' -> '2026-09-17 12:02' (Werte sind Ortszeit mit Z-Suffix)."""
    m = _ISO_RE.match(str(s or ""))
    return f"{m.group(1)} {m.group(2)}" if m else None


def _neu(name, datum):
    return {"name": name, "datum": datum, "personen": 0, "teams": 0, "bestellung": "",
            "bestellt_am": "", "status": "", "start": None, "ziel": None, "codes": [], "foto_url": None}


def _merge(e, personen, start=None, ziel=None, code=None, foto_url=None):
    e["personen"] += personen
    e["teams"] += 1
    if start and (e["start"] is None or start < e["start"]):
        e["start"] = start
    if ziel and (e["ziel"] is None or ziel > e["ziel"]):
        e["ziel"] = ziel
    if code and code not in e["codes"]:
        e["codes"].append(code)
    if foto_url and not e["foto_url"]:
        e["foto_url"] = foto_url


def _parse_json(daten):
    """Kontodaten-JSON (siehe Modul-Doku) -> dieselbe Eintragsliste wie beim Text."""
    orders = daten.get("orders", []) if isinstance(daten, dict) else daten
    out = []
    for o in orders or []:
        if not isinstance(o, dict):
            continue
        status = str(o.get("status") or "")
        status = _STATUS_LABEL.get(status.lower(), status)
        je_trail = {}
        for team in ((o.get("booking") or {}).get("teams") or []):
            name = re.sub(r"\s+", " ", str((team.get("trail") or {}).get("name") or "")).strip()
            if not name:
                continue
            slot = (team.get("slot") or {}).get("date_time") or team.get("start_time_actual") or ""
            datum = str(slot)[:10] if re.match(r"\d{4}-\d{2}-\d{2}", str(slot)) else None
            personen = (team.get("confirmed_adult_tickets") or 0) + (team.get("confirmed_child_tickets") or 0)
            if not personen:
                personen = sum(int(p.get("quantity") or 0) for p in (team.get("participants_booked") or []))
            e = je_trail.setdefault((name, datum), _neu(name, datum))
            foto = str(team.get("img_public_url") or "")
            _merge(e, personen, _zeit(team.get("start_time_actual")), _zeit(team.get("finish_time")),
                   str(team.get("code") or "").strip() or None,
                   foto if foto.startswith("https://") else None)
        for e in je_trail.values():
            e.update({"bestellung": str(o.get("id") or ""), "bestellt_am": str(o.get("date_created") or "")[:10],
                      "status": status})
            out.append(e)
    return out


def parse(inhalt):
    """Liste von Eintraegen mit den Schluesseln FELDER (siehe Modul-Doku)."""
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
            cm = _CODE_RE.search(seg)
            e = je_trail.setdefault((name, datum), _neu(name, datum))
            _merge(e, personen, code=cm.group(1) if cm else None)
        for e in je_trail.values():
            e.update({"bestellung": h.group(2), "bestellt_am": _iso(*h.group(1).split(".")),
                      "status": status})
            out.append(e)
    return out


def eintraege_aus_json(s, limit=500):
    """Eintragsliste aus dem versteckten Formularfeld der Import-Seite zurueckholen.
    Nimmt nur die bekannten Felder mit passenden Typen; wirft ValueError bei Unsinn."""
    daten = json.loads(s or "[]")
    if not isinstance(daten, list):
        raise ValueError("Liste erwartet")
    out = []
    for d in daten[:limit]:
        if not isinstance(d, dict) or not d.get("name"):
            raise ValueError("Eintrag ohne Name")
        e = _neu(str(d["name"])[:100], str(d["datum"])[:10] if d.get("datum") else None)
        e["personen"] = int(d.get("personen") or 0)
        e["teams"] = int(d.get("teams") or 0)
        e["bestellung"] = str(d.get("bestellung") or "")[:20]
        e["bestellt_am"] = str(d.get("bestellt_am") or "")[:10]
        e["status"] = str(d.get("status") or "")[:30]
        e["start"] = str(d["start"])[:16] if d.get("start") else None
        e["ziel"] = str(d["ziel"])[:16] if d.get("ziel") else None
        e["codes"] = [str(c)[:12] for c in (d.get("codes") or []) if c][:10]
        foto = str(d.get("foto_url") or "")
        e["foto_url"] = foto[:300] if foto.startswith("https://") else None
        out.append(e)
    return out


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _fehlt_etwas(t, e):
    """Bereits gemachter Trail: gibt es Zusatzdaten im Eintrag, die im Trail noch fehlen?"""
    return bool((e["start"] and not t.get("start_zeit")) or (e["foto_url"] and not t.get("foto"))
                or (e["codes"] and not t.get("team_code")) or (e["bestellung"] and not t.get("bestellung")))


def zuordnen(conn, eintraege, heute=None):
    """Jeden Eintrag einem Trail zuordnen und entscheiden, was passieren wuerde.
    Ergibt Liste von (eintrag, trail_oder_None, aktion, grund) mit aktion in
    'setzen' | 'ergaenzen' | 'uebersprungen' | 'unbekannt'."""
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
        elif t["gemacht"] and _fehlt_etwas(t, e):
            plan.append((e, t, "ergaenzen", "bereits gemacht, Zeiten/Foto/Code werden nachgetragen"))
        elif t["gemacht"]:
            plan.append((e, t, "uebersprungen",
                         f"bereits als gemacht erfasst ({t['gemacht_datum'] or 'ohne Datum'})"))
        else:
            plan.append((e, t, "setzen", ""))
    return plan


def lade_foto(url, foto_dir, trail_id):
    """Schlussfoto holen und als <trail_id>.jpg|png ablegen. Gibt den Dateinamen zurueck,
    None bei jedem Fehler - ein fehlendes Foto darf den Import nie abbrechen."""
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if not ct.startswith("image/") or len(r.content) > MAX_FOTO_BYTES:
            return None
        name = f"{trail_id}.png" if "png" in ct else f"{trail_id}.jpg"
        os.makedirs(foto_dir, exist_ok=True)
        with open(os.path.join(foto_dir, name), "wb") as fh:
            fh.write(r.content)
        return name
    except (requests.RequestException, OSError):
        return None


def anwenden(conn, plan, benutzer="import", foto_dir=None):
    """setzen/ergaenzen ausfuehren; Bemerkung bleibt erhalten. foto_dir=None: keine Fotos laden.
    Gibt {'gesetzt', 'ergaenzt', 'fotos', 'foto_fehler'} zurueck."""
    res = {"gesetzt": 0, "ergaenzt": 0, "fotos": 0, "foto_fehler": 0}
    for e, t, aktion, _ in plan:
        if aktion == "setzen":
            trails.update_done(conn, t["id"], {
                "gemacht": "1", "gemacht_datum": e["datum"],
                "mitspieler": str(e["personen"]) if e["personen"] else "",
                "bemerkung": t["bemerkung"]}, benutzer)
            res["gesetzt"] += 1
        elif aktion == "ergaenzen":
            res["ergaenzt"] += 1
        else:
            continue
        trails.set_import(conn, t["id"], start=e["start"], ziel=e["ziel"],
                          code=", ".join(e["codes"]) or None, bestellung=e["bestellung"] or None,
                          foto_url=e["foto_url"])
        if foto_dir and e["foto_url"] and not t.get("foto"):
            name = lade_foto(e["foto_url"], foto_dir, t["id"])
            if name:
                trails.set_foto(conn, t["id"], name)
                res["fotos"] += 1
            else:
                res["foto_fehler"] += 1
    return res
