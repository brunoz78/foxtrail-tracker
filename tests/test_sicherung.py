# -*- coding: utf-8 -*-
"""Sicherung: alles in eine ZIP-Datei - und wieder zurueck."""

import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxtrail import authlog, db, sicherung, trails, users  # noqa: E402
from tests.test_app import app, login  # noqa: E402,F401  (Fixture)


def _eigener_fotoordner(app, tmp_path):
    """Nie in den echten Fotoordner der Entwicklungsumgebung schreiben."""
    app.config["FOTO_DIR"] = str(tmp_path / "fotos")
    os.makedirs(app.config["FOTO_DIR"], exist_ok=True)


def _foto(app, name="1.jpg", inhalt=b"nicht wirklich ein JPEG"):
    os.makedirs(app.config["FOTO_DIR"], exist_ok=True)
    pfad = os.path.join(app.config["FOTO_DIR"], name)
    with open(pfad, "wb") as fh:
        fh.write(inhalt)
    return pfad


def _sichern(app, tmp_path):
    ziel = str(tmp_path / "sicherung.zip")
    meta = sicherung.erstellen(ziel, app.config["DB_PATH"], app.config["FOTO_DIR"])
    return ziel, meta


def test_erstellen_und_einlesen(app, tmp_path):
    _eigener_fotoordner(app, tmp_path)
    _foto(app)
    _foto(app, "klein.jpg")
    os.makedirs(os.path.join(app.config["FOTO_DIR"], "titel"), exist_ok=True)   # Cache bleibt draussen
    ziel, meta = _sichern(app, tmp_path)
    with zipfile.ZipFile(ziel) as z:
        namen = z.namelist()
    assert "meta.json" in namen and "foxtrail.db" in namen
    assert sorted(n for n in namen if n.startswith("fotos/")) == ["fotos/1.jpg", "fotos/klein.jpg"]
    assert (meta["trails"], meta["benutzer"], meta["fotos"]) == (2, 2, 2)

    # alles kaputt machen: Trails weg, Benutzer dazu, Foto weg
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("DELETE FROM trails")
        users.add(conn, "neu", "geheim123")
    os.remove(os.path.join(app.config["FOTO_DIR"], "1.jpg"))

    zahlen = sicherung.einlesen(ziel, app.config["DB_PATH"], app.config["FOTO_DIR"])
    assert (zahlen["trails"], zahlen["benutzer"], zahlen["fotos"]) == (2, 2, 2)
    assert os.path.exists(zahlen["alt"])                       # bisherige DB bleibt liegen
    assert os.path.exists(os.path.join(app.config["FOTO_DIR"], "1.jpg"))
    with db.session(app.config["DB_PATH"]) as conn:
        assert len(trails.list_active(conn)) == 2
        assert users.get(conn, "neu") is None and users.get(conn, "admin")


def test_einlesen_prueft_die_datei(app, tmp_path):
    _eigener_fotoordner(app, tmp_path)
    murks = str(tmp_path / "murks.zip")
    with open(murks, "wb") as fh:
        fh.write(b"kein ZIP")
    with pytest.raises(sicherung.SicherungFehler):
        sicherung.einlesen(murks, app.config["DB_PATH"], app.config["FOTO_DIR"])
    leer = str(tmp_path / "leer.zip")
    with zipfile.ZipFile(leer, "w") as z:
        z.writestr("irgendwas.txt", "hallo")
    with pytest.raises(sicherung.SicherungFehler):
        sicherung.einlesen(leer, app.config["DB_PATH"], app.config["FOTO_DIR"])
    # die eigene Datenbank ist noch da
    with db.session(app.config["DB_PATH"]) as conn:
        assert len(trails.list_active(conn)) == 2


def test_seite_und_download(app, tmp_path):
    _eigener_fotoordner(app, tmp_path)
    c = app.test_client()
    login(c, "gast")
    assert c.get("/admin/sicherung").status_code == 403
    a = app.test_client()
    login(a)
    html = a.get("/admin/sicherung").get_data(as_text=True)
    assert "Sicherung herunterladen" in html and "Ja, alle jetzigen Daten ersetzen" in html
    r = a.get("/admin/sicherung/datei")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert "foxtrail-sicherung-" in r.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert "foxtrail.db" in z.namelist()
    with db.session(app.config["DB_PATH"]) as conn:
        assert ("admin", "backup") in [(r["benutzer"], r["ereignis"]) for r in authlog.lesen(conn)]


def test_einlesen_ueber_die_seite(app, tmp_path):
    _eigener_fotoordner(app, tmp_path)
    _foto(app)
    ziel, _ = _sichern(app, tmp_path)
    with open(ziel, "rb") as fh:
        inhalt = fh.read()
    c = app.test_client()
    login(c)
    # ohne Haken passiert nichts
    r = c.post("/admin/sicherung/einlesen",
               data={"datei": (io.BytesIO(inhalt), "s.zip")}, follow_redirects=True)
    assert "Bitte bestätigen" in r.get_data(as_text=True)
    # Unsinn wird sauber abgelehnt
    r = c.post("/admin/sicherung/einlesen",
               data={"datei": (io.BytesIO(b"kein zip"), "s.zip"), "bestaetigt": "1"},
               follow_redirects=True)
    assert "konnte nicht eingelesen werden" in r.get_data(as_text=True)
    assert c.get("/").status_code == 200                       # noch angemeldet, nichts passiert
    # richtig: einlesen, danach abgemeldet
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("DELETE FROM trails")
    r = c.post("/admin/sicherung/einlesen",
               data={"datei": (io.BytesIO(inhalt), "s.zip"), "bestaetigt": "1"},
               follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "Sicherung eingelesen: 2 Trails" in text and "Anmelden" in text
    assert c.get("/").status_code == 302                       # Sitzung ist zu Ende
    with db.session(app.config["DB_PATH"]) as conn:
        assert len(trails.list_active(conn)) == 2
        assert ("admin", "restore") in [(r["benutzer"], r["ereignis"]) for r in authlog.lesen(conn)]
