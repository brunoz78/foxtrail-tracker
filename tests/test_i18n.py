# -*- coding: utf-8 -*-
"""Uebersetzungen: jeder Text der Oberflaeche steht in jedem Katalog (foxtrail/i18n/*.json),
Platzhalter stimmen ueberein, und die Seiten erscheinen in der gewaehlten Sprache."""

import ast
import glob
import json
import os
import re
import string
import sys

import pytest
from jinja2 import Environment, nodes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxtrail import authlog, bestellungen, db, i18n, trails, users, zeitplan  # noqa: E402
from tests.test_app import app  # noqa: E402,F401  (Fixture)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def texte():
    """Alle deutschen Ausgangstexte: _('...') in Templates, tr("...") im Code, Beschriftungen
    aus Konstanten, die erst zur Laufzeit uebersetzt werden."""
    gefunden = set()
    env = Environment()
    for pfad in glob.glob(os.path.join(REPO, "foxtrail", "templates", "*.html")):
        baum = env.parse(open(pfad, encoding="utf-8").read())
        for c in baum.find_all(nodes.Call):
            if isinstance(c.node, nodes.Name) and c.node.name == "_" and c.args \
                    and isinstance(c.args[0], nodes.Const) and isinstance(c.args[0].value, str):
                gefunden.add(c.args[0].value)
    for pfad in glob.glob(os.path.join(REPO, "foxtrail", "*.py")):
        for n in ast.walk(ast.parse(open(pfad, encoding="utf-8").read())):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" and n.args \
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                gefunden.add(n.args[0].value)
    gefunden |= set(trails.TYP_LABEL.values()) | set(trails.GRAD_LABEL.values())
    gefunden |= set(trails.REGION_LABEL.values()) | {label for _, label, _ in trails.SPALTEN}
    gefunden |= set(users.ROLLEN.values()) | set(zeitplan.ZYKLEN.values()) | set(i18n.WOCHENTAGE)
    gefunden |= set(bestellungen._STATUS_LABEL.values()) | {"ohne Angabe", "nicht erfasst"}   # trails.filter_options
    gefunden |= set(authlog.EREIGNISSE.values()) | {g for g, _ in authlog.GRUPPEN}
    return gefunden


def _platzhalter(s):
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


@pytest.mark.parametrize("sprache", [s for s in i18n.SPRACHEN if s != i18n.STANDARD])
def test_katalog_vollstaendig(sprache):
    kat = json.load(open(os.path.join(REPO, "foxtrail", "i18n", f"{sprache}.json"), encoding="utf-8"))
    alle = texte()
    fehlt = sorted(alle - set(kat))
    assert not fehlt, f"{sprache}: {len(fehlt)} Texte fehlen, z. B. {fehlt[:5]}"
    ueberzaehlig = sorted(set(kat) - alle)
    assert not ueberzaehlig, f"{sprache}: nicht mehr benutzt: {ueberzaehlig[:5]}"
    for de, fremd in kat.items():
        assert fremd.strip(), (sprache, de)
        assert _platzhalter(de) == _platzhalter(fremd), (sprache, de, fremd)


def test_texte_sind_eindeutig_deutsch():
    # Ausgangstexte mit Umlauten statt ae/oe/ue (Arbeitsregel "Umlaute")
    for t in texte():
        assert not re.search(r"\b(ae|oe|ue)\b", t), t



def test_sprache_waehlen(app):
    from tests.test_app import login
    c = app.test_client()
    # Anmeldeseite: Browsersprache, dann Wahl per Knopf (bleibt nach der Anmeldung)
    assert "Se connecter" in c.get("/login", headers={"Accept-Language": "fr-CH,fr;q=0.9"}).get_data(as_text=True)
    assert "Anmelden" in c.get("/login").get_data(as_text=True)
    c.post("/sprache", data={"sprache": "it", "next": "/login"})
    assert "Accedi" in c.get("/login").get_data(as_text=True)
    login(c)
    html = c.get("/").get_data(as_text=True)
    assert 'lang="it"' in html and "Tutti i trail" in html and "Statistiche" in html
    # angemeldet: Wahl wird beim Benutzer gespeichert und gilt auch in einer neuen Sitzung
    c.post("/sprache", data={"sprache": "en", "next": "/"})
    with db.session(app.config["DB_PATH"]) as conn:
        assert users.get(conn, "admin")["sprache"] == "en"
    c2 = app.test_client()
    login(c2)
    html = c2.get("/statistik").get_data(as_text=True)
    assert "Statistics" in html and "Statistik</h1>" not in html
    # Meldungen und Fehlertexte aus dem Code
    r = c2.post("/trail/1", data={"gemacht": "1", "next": "/"}, follow_redirects=True)
    assert "Please enter the date you did the trail." in r.get_data(as_text=True)
    assert "Page not found." in c2.get("/gibt-es-nicht").get_data(as_text=True)
    # Admin stellt die Sprache eines Benutzers auf "automatisch" zurueck
    c2.post("/admin/benutzer/speichern", data={"modus": "bearbeiten", "username": "admin", "rolle": "admin",
                                               "pw_modus": "frei", "aktiv": "1", "sprache": ""})
    with db.session(app.config["DB_PATH"]) as conn:
        assert users.get(conn, "admin")["sprache"] is None
    c2.post("/sprache", data={"sprache": "xx"})                           # Unsinn wird ignoriert
    with db.session(app.config["DB_PATH"]) as conn:
        assert users.get(conn, "admin")["sprache"] is None


def test_ausserhalb_einer_anfrage_deutsch():
    assert i18n.tr("Speichern") == "Speichern"
    assert i18n.tr("Passwort muss mindestens {n} Zeichen haben.", n=8) == "Passwort muss mindestens 8 Zeichen haben."


if __name__ == "__main__":                      # fehlende Texte als JSON-Geruest ausgeben
    sprache = sys.argv[1] if len(sys.argv) > 1 else "en"
    pfad = os.path.join(REPO, "foxtrail", "i18n", f"{sprache}.json")
    kat = json.load(open(pfad, encoding="utf-8")) if os.path.exists(pfad) else {}
    print(json.dumps({t: "" for t in sorted(texte() - set(kat))}, ensure_ascii=False, indent=1))
