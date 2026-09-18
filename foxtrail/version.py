# -*- coding: utf-8 -*-
"""
Versionsnummer und Hinweis auf neue Releases.

VERSION wird vor jedem Release angehoben (gleich wie der Git-Tag, ohne "v").

Update-Hinweis: Der Server fragt hoechstens alle 12 Stunden bei der GitHub-API nach dem
neuesten Release (im Hintergrund, blockiert keine Seite) und merkt sich das Ergebnis in
<Datenordner>/update.json. Uebertragen wird dabei nichts ausser der Anfrage selbst.
Abschalten mit FOXTRAIL_UPDATE_CHECK=0.
"""

import json
import os
import re
import threading
import time

VERSION = "1.3.1"
REPO = "brunoz78/foxtrail-tracker"
REPO_URL = f"https://github.com/{REPO}"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
INTERVALL = 12 * 3600        # erfolgreiche Pruefung gilt so lange
INTERVALL_FEHLER = 3600      # nach einem Fehler frueher wieder versuchen

_laeuft = threading.Lock()


def stand(repo_root):
    """Anzeige fuer "Über": '1.3.0' oder '1.3.0 (Entwicklungsstand main@abc1234)'.
    deploy/dev-update.sh legt die Datei DEV_STAND an; ein Release-Update entfernt sie."""
    try:
        with open(os.path.join(repo_root, "DEV_STAND"), encoding="utf-8") as fh:
            dev = fh.read().strip()
    except OSError:
        dev = ""
    return f"{VERSION} (Entwicklungsstand {dev})" if dev else VERSION


def _tupel(v):
    """'v1.10.2' -> (1, 10, 2); Unlesbares -> None."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None


def neuer(verfuegbar, installiert=VERSION):
    a, b = _tupel(verfuegbar), _tupel(installiert)
    return bool(a and b and a > b)


def datei(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "update.json")


def _lesen(pfad):
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _abrufen(pfad, alt):
    import requests
    d = {"geprueft": time.time(), "version": alt.get("version"), "url": alt.get("url")}
    try:
        r = requests.get(API_URL, timeout=8, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": f"foxtrail-tracker/{VERSION}"})
        r.raise_for_status()
        j = r.json()
        d.update(version=(j.get("tag_name") or "").lstrip("v"), url=j.get("html_url"), fehler=False)
    except Exception:            # offline, Rate-Limit, ... -> spaeter nochmals
        d["fehler"] = True
    try:
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(tmp, pfad)
    except OSError:
        pass


def _hintergrund(pfad, alt):
    try:
        _abrufen(pfad, alt)
    finally:
        _laeuft.release()


def verfuegbar(pfad, pruefen=True):
    """{'version': '1.4.0', 'url': ...} wenn es ein neueres Release gibt, sonst None.
    Liest nur den Zwischenspeicher; ist er veraltet und pruefen=True, wird im Hintergrund
    neu abgefragt (das Ergebnis erscheint beim naechsten Seitenaufruf)."""
    d = _lesen(pfad)
    if pruefen:
        alter = time.time() - float(d.get("geprueft") or 0)
        if alter > (INTERVALL_FEHLER if d.get("fehler") else INTERVALL) and _laeuft.acquire(blocking=False):
            threading.Thread(target=_hintergrund, args=(pfad, d), daemon=True).start()
    if neuer(d.get("version")):
        return {"version": d["version"], "url": d.get("url") or f"{REPO_URL}/releases/latest"}
    return None
