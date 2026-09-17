# -*- coding: utf-8 -*-
"""End-to-End ueber den Flask-Testclient (temporaere SQLite-Datei)."""

import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import foxtrail  # noqa: E402
from foxtrail import db, sync, trails, users  # noqa: E402
from tests.test_sync import mk  # noqa: E402


@pytest.fixture
def app(tmp_path):
    foxtrail._ATTEMPTS.clear()
    path = str(tmp_path / "t.db")
    app = foxtrail.create_app({"DB_PATH": path, "TESTING": True, "SECRET_KEY": "test"})
    with db.session(path) as conn:
        users.add(conn, "admin", "geheim123", is_admin=True)
        users.add(conn, "gast", "geheim123")
        sync.apply(conn, [mk("aargau/aquae", ort="Baden", name="Aquae"),
                          mk("wallis/simplon", ort="Brig", name="Simplon")], "test")
    return app


def login(c, name="admin", pw="geheim123"):
    return c.post("/login", data={"username": name, "password": pw}, follow_redirects=True)


def test_login_erforderlich(app):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    r = login(c, pw="falsch")
    assert "falsch" in r.get_data(as_text=True)
    r = login(c)
    assert r.status_code == 200 and "Aquae" in r.get_data(as_text=True)


def test_sperre_nach_fehlversuchen(app):
    c = app.test_client()
    for _ in range(5):
        login(c, pw="x")
    r = login(c)                                   # richtiges Passwort, aber gesperrt
    assert "Zu viele Fehlversuche" in r.get_data(as_text=True)
    assert c.get("/").status_code == 302


def test_bearbeiten_und_archiv(app):
    c = app.test_client()
    login(c, "gast")
    r = c.post("/trail/1", data={"gemacht": "1", "gemacht_datum": "2025-08-10", "mitspieler": "5",
                                 "bemerkung": "Regen", "next": "/"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Gespeichert" in html and "10.08.2025" in html and "Regen" in html
    # Simplon verschwindet von der Website -> Archiv; Aquae verschwindet -> bleibt (gemacht)
    with db.session(app.config["DB_PATH"]) as conn:
        sync.apply(conn, [mk("a/neu")], "test")
    html = c.get("/").get_data(as_text=True)
    assert "Aquae" in html and "nicht mehr im Angebot seit" in html and "Simplon" not in html
    assert "Neu ab " in html                             # a/neu kam per Abgleich dazu
    assert "Neu ab " in c.get("/trail/3").get_data(as_text=True)
    html = c.get("/archiv").get_data(as_text=True)
    assert "Simplon" in html
    # aus dem Archiv als gemacht markieren -> zurueck in die Liste
    c.post("/trail/2", data={"gemacht": "1", "next": "/archiv"})
    assert "Simplon" in c.get("/?f=gemacht").get_data(as_text=True)
    assert "Simplon" not in c.get("/archiv").get_data(as_text=True)


def test_validierung(app):
    c = app.test_client()
    login(c)
    r = c.post("/trail/1", data={"gemacht": "1", "mitspieler": "abc", "next": "/"})
    assert "ganze Zahl" in r.get_data(as_text=True)
    with db.session(app.config["DB_PATH"]) as conn:
        assert trails.get(conn, 1)["gemacht"] == 0


def test_manueller_trail(app):
    c = app.test_client()
    login(c, "gast")
    r = c.post("/trail/neu", data={"ort": "Altdorf", "name": "Tell", "gemacht": "1", "region": "luzern-und-umgebung",
                                   "gemacht_datum": "2018-07-01", "mitspieler": "2"}, follow_redirects=True)
    assert "manuell erfasst" in r.get_data(as_text=True)
    html = c.get("/").get_data(as_text=True)
    assert "Tell" in html and "manuell" in html and "Luzern und Umgebung" in html
    assert "Tell" in c.get("/?region=luzern-und-umgebung").get_data(as_text=True)
    r = c.post("/trail/3/loeschen", follow_redirects=True)
    assert "geloescht" in r.get_data(as_text=True)
    assert c.post("/trail/1/loeschen", follow_redirects=True).status_code == 200
    with db.session(app.config["DB_PATH"]) as conn:
        assert trails.get(conn, 1) is not None       # foxtrail-Trail nicht loeschbar


def test_admin_rechte(app):
    c = app.test_client()
    login(c, "gast")
    assert c.get("/admin/benutzer").status_code == 403
    assert c.get("/admin/sync").status_code == 403
    login(c)
    assert c.get("/admin/benutzer").status_code == 200
    r = c.post("/admin/benutzer/anlegen", data={"username": "neu", "password": "geheim123"},
               follow_redirects=True)
    assert "angelegt" in r.get_data(as_text=True)
    r = c.post("/admin/benutzer/admin", data={"action": "admin", "is_admin": ""}, follow_redirects=True)
    assert "mindestens ein aktiver Administrator" in r.get_data(as_text=True)
    r = c.post("/admin/benutzer/neu", data={"action": "loeschen"}, follow_redirects=True)
    assert "geloescht" in r.get_data(as_text=True)


def test_passwort_aendern(app):
    c = app.test_client()
    login(c, "gast")
    r = c.post("/profil", data={"old": "geheim123", "new": "neuesPw123", "new2": "neuesPw123"},
               follow_redirects=True)
    assert "geaendert" in r.get_data(as_text=True)
    c.get("/logout")
    assert "Aquae" in login(c, "gast", "neuesPw123").get_data(as_text=True)


def test_sync_seite(app, monkeypatch):
    c = app.test_client()
    login(c)
    from foxtrail import scraper
    monkeypatch.setattr(scraper, "fetch_all", lambda: [mk("aargau/aquae", ort="Baden", name="Aquae"),
                                                       mk("wallis/simplon", ort="Brig", name="Simplon"),
                                                       mk("jura/neu", ort="Delsberg", name="Neu")])
    r = c.post("/admin/sync", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Abgleich ok" in html and "1 neu" in html and "web:admin" in html
    monkeypatch.setattr(scraper, "fetch_all", lambda: (_ for _ in ()).throw(scraper.ScrapeError("kaputt")))
    r = c.post("/admin/sync", follow_redirects=True)
    assert "fehlgeschlagen: kaputt" in r.get_data(as_text=True)


def test_liste_sortieren_filtern(app):
    c = app.test_client()
    login(c)
    with db.session(app.config["DB_PATH"]) as conn:
        sync.apply(conn, [mk("aargau/aquae", ort="Baden", name="Aquae", preis=32.0),
                          mk("wallis/simplon", ort="Brig", name="Simplon", preis=36.0,
                             schwierigkeit="schwierig"),
                          mk("ostschweiz/baccara-mini", ort="Rapperswil", name="Baccara Mini",
                             typ="mini", preis=19.0, dauer="1-2 Stunden", schwierigkeit="einfach")], "test")

    def reihenfolge(url):
        html = c.get(url).get_data(as_text=True)
        return sorted(["Aquae", "Simplon", "Baccara Mini"], key=html.index)

    assert reihenfolge("/") == ["Aquae", "Simplon", "Baccara Mini"]
    assert reihenfolge("/?sort=preis&dir=desc") == ["Simplon", "Aquae", "Baccara Mini"]
    assert reihenfolge("/?sort=kaputt&dir=egal") == ["Aquae", "Simplon", "Baccara Mini"]
    html = c.get("/?typ=mini&typ=go").get_data(as_text=True)
    assert "Baccara Mini" in html and "Aquae" not in html and "typ-mini" in html
    html = c.get("/?region=aargau&region=wallis").get_data(as_text=True)
    assert "Aquae" in html and "Simplon" in html and "Baccara Mini" not in html
    html = c.get("/?dauer=1-2+Stunden").get_data(as_text=True)
    assert "Baccara Mini" in html and "Simplon" not in html and "1–2 h" in html
    html = c.get("/?sort=region").get_data(as_text=True)
    assert 'class="grp"' in html and "Ostschweiz" in html
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("UPDATE trails SET neu_seit = NULL")   # Fixture legt alles per Abgleich an
    html = c.get("/?f=neu").get_data(as_text=True)
    assert "Keine Trails gefunden" in html
    with db.session(app.config["DB_PATH"]) as conn:
        sync.apply(conn, [mk("aargau/aquae", ort="Baden", name="Aquae"),
                          mk("wallis/simplon", ort="Brig", name="Simplon", schwierigkeit="schwierig"),
                          mk("ostschweiz/baccara-mini", ort="Rapperswil", name="Baccara Mini", typ="mini",
                             schwierigkeit="einfach"),
                          mk("jura/frisch", ort="Delsberg", name="Frisch")], "test")
    html = c.get("/?f=neu").get_data(as_text=True)
    assert "Frisch" in html and "Aquae" not in html and "Neu ab " in html
    html = c.get("/?grad=schwierig").get_data(as_text=True)
    assert "Simplon" in html and "Aquae" not in html and "grad-schwierig" in html
    assert reihenfolge("/?sort=schwierigkeit") == ["Baccara Mini", "Aquae", "Simplon"]
    # Sortierung bleibt beim Suchen erhalten (hidden fields), Links tragen den Zustand mit
    html = c.get("/?sort=preis&dir=desc&q=a").get_data(as_text=True)
    assert 'name="sort" value="preis"' in html and "sort=preis" in html


def test_import_seite(app, monkeypatch, tmp_path):
    from foxtrail import bestellungen
    from tests.test_bestellungen import MUSTER_JSON, _Antwort
    app.config["FOTO_DIR"] = str(tmp_path / "fotos")
    monkeypatch.setattr(bestellungen.requests, "get", lambda url, timeout=None, headers=None: _Antwort())
    c = app.test_client()
    login(c, "gast")
    assert "konto.json" in c.get("/import").get_data(as_text=True)
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("UPDATE trails SET name = 'Columban' WHERE slug = 'aargau/aquae'")
    daten = json.dumps(MUSTER_JSON).encode("utf-8")
    r = c.post("/import", data={"datei": (io.BytesIO(daten), "konto.json")}, content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert "eintragen</span>" in html and "Columban" in html and "unbekannt" in html   # Hera, Granit
    assert 'name="daten"' in html and "2:50 h" in html
    # Schritt 2 mit den geprueften Daten
    eintraege = bestellungen.parse(daten.decode("utf-8"))
    r = c.post("/import", data={"schritt": "schreiben", "daten": json.dumps(eintraege)}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "1 als gemacht eingetragen" in html and "1 Schlussfoto" in html
    with db.session(app.config["DB_PATH"]) as conn:
        t = trails.get(conn, 1)
    assert t["gemacht"] == 1 and t["spielzeit"] == "2:50 h" and t["foto"] == "1.jpg" and t["team_code"] == "AAAAAA"
    r = c.get("/foto/1")
    assert r.status_code == 200 and r.data == b"\xff\xd8bild"
    assert c.get("/foto/2").status_code == 404
    html = c.get("/trail/1").get_data(as_text=True)
    assert "2:50 h" in html and "AAAAAA" in html and "/foto/1" in html and "418324" in html
    assert "2:50 h" in c.get("/?sort=spielzeit").get_data(as_text=True)
    assert "Keine Bestellungen gefunden" in c.post("/import", data={"text": "nix"}).get_data(as_text=True)
    r = c.post("/import", data={"schritt": "schreiben", "daten": "{"}, follow_redirects=True)
    assert "Ungueltige Daten" in r.get_data(as_text=True)
    c.get("/logout")
    assert c.get("/foto/1").status_code == 302                    # nur angemeldet


def test_healthz(app):
    assert app.test_client().get("/healthz").data == b"ok"
