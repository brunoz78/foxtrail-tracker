# -*- coding: utf-8 -*-
"""Sicherheit: CSRF, Weiterleitungen, Kopfzeilen, Client-IP hinter dem Proxy, Sperre ueber alle
Prozesse, TOTP nur einmal, Foto-Download nur aus dem Internet, ZIP-Bombe."""

import io
import os
import re
import socket
import sys
import zipfile

import pyotp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import foxtrail  # noqa: E402
from foxtrail import authlog, bestellungen, config, db, sicherung, sync, twofa, users  # noqa: E402
from tests.test_app import login  # noqa: E402
from tests.test_bestellungen import _Antwort  # noqa: E402
from tests.test_sync import mk  # noqa: E402


def _app(tmp_path, **cfg):
    pfad = str(tmp_path / "t.db")
    app = foxtrail.create_app({"DB_PATH": pfad, "TESTING": True, "SECRET_KEY": "test", **cfg})
    with db.session(pfad) as conn:
        if not users.get(conn, "admin"):
            users.add(conn, "admin", "geheim123", is_admin=True)
            sync.apply(conn, [mk("aargau/aquae", ort="Baden", name="Aquae")], "test")
    return app


def _token(html):
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def test_csrf(tmp_path):
    app = _app(tmp_path, CSRF=True)
    c = app.test_client()
    seite = c.get("/login").get_data(as_text=True)
    token = _token(seite)
    assert seite.count('name="csrf" value="' + token + '"') == 2          # Anmeldung und Sprachwahl
    # ohne Token: abgewiesen, nicht angemeldet
    r = c.post("/login", data={"username": "admin", "password": "geheim123"})
    assert r.status_code == 400 and "nicht bestätigt werden" in r.get_data(as_text=True)
    r = c.post("/login", data={"username": "admin", "password": "geheim123", "csrf": "falsch"})
    assert r.status_code == 400 and c.get("/").status_code == 302
    # Passkey-Abfrage (JSON) ohne Header: Fehler als JSON
    r = c.post("/login/passkey/verify", data="{}", content_type="application/json")
    assert r.status_code == 400 and "nicht bestätigt" in r.get_json()["error"]
    # mit Token: angemeldet; danach gilt ein neues Token (neue Sitzung)
    r = c.post("/login", data={"username": "admin", "password": "geheim123", "csrf": token})
    assert r.status_code == 302 and c.get("/").status_code == 200
    neu = _token(c.get("/trail/1").get_data(as_text=True))
    assert neu != token
    assert c.post("/trail/1", data={"bemerkung": "x", "csrf": token}).status_code == 400
    r = c.post("/trail/1", data={"bemerkung": "x", "csrf": neu})
    assert r.status_code == 302
    # Header statt Feld (so schickt es webauthn.js)
    r = c.post("/spalten", data={"aktion": "standard"}, headers={"X-CSRF-Token": neu})
    assert r.status_code == 302
    # jedes POST-Formular traegt das Feld
    for pfad in ("/", "/trail/1", "/trail/neu", "/passwort", "/profil", "/admin/benutzer", "/import",
                 "/admin/sync", "/admin/sicherung"):
        html = c.get(pfad).get_data(as_text=True)
        formulare = re.findall(r'<form\b[^>]*?method="post"[^>]*>(.{0,120})', html, re.S)
        assert formulare and all(f.startswith('<input type="hidden" name="csrf"') for f in formulare), pfad


def test_weiterleitung_nur_innerhalb(tmp_path):
    app = _app(tmp_path)
    c = app.test_client()
    login(c)
    html = c.get("/trail/1?next=https://example.invalid/x").get_data(as_text=True)
    werte = re.findall(r'name="next" value="([^"]*)"', html)
    assert werte[0].startswith("/trail/1?")           # Sprachwahl: eigene Adresse, /sprache prueft selbst
    assert set(werte[1:]) == {"/"}                    # Trail-Formulare uebernehmen kein fremdes Ziel
    for fremd in ("https://example.invalid/x", "//example.invalid/x", "/\\example.invalid", "javascript:alert(1)"):
        r = c.post("/trail/1", data={"bemerkung": "x", "next": fremd})
        assert r.headers["Location"] == "/", fremd
        r = c.post("/spalten", data={"aktion": "standard", "next": fremd})
        assert r.headers["Location"] == "/", fremd
    r = c.post("/trail/1", data={"bemerkung": "x", "next": "/statistik?a=1"})
    assert r.headers["Location"] == "/statistik?a=1"


def test_kopfzeilen(tmp_path):
    r = _app(tmp_path).test_client().get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_ip_vom_proxy(tmp_path, monkeypatch):
    # nginx haengt die echte Adresse hinten an; den Anfang kann der Client selbst setzen
    app = _app(tmp_path)
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "falsch"},
           headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.7"}, environ_base={"REMOTE_ADDR": "10.0.0.2"})
    with db.session(app.config["DB_PATH"]) as conn:
        assert authlog.lesen(conn, 50, None, "fail")[0]["ip"] == "203.0.113.7"
    # ohne Proxy (FOXTRAIL_PROXIES=0) zaehlt X-Forwarded-For gar nicht
    monkeypatch.setenv("FOXTRAIL_PROXIES", "0")
    (tmp_path / "ohne").mkdir()
    app = _app(tmp_path / "ohne")
    app.test_client().post("/login", data={"username": "admin", "password": "falsch"},
                           headers={"X-Forwarded-For": "6.6.6.6"}, environ_base={"REMOTE_ADDR": "10.0.0.2"})
    with db.session(app.config["DB_PATH"]) as conn:
        assert authlog.lesen(conn, 50, None, "fail")[0]["ip"] == "10.0.0.2"


def test_sperre_gilt_fuer_alle_prozesse(tmp_path):
    # zwei Apps auf derselben Datenbank = zwei gunicorn-Prozesse
    eins, zwei = _app(tmp_path), _app(tmp_path)
    for _ in range(config.LOGIN_MAX_FAILS):
        login(eins.test_client(), pw="falsch")
    assert "Zu viele Fehlversuche" in login(zwei.test_client()).get_data(as_text=True)
    with db.session(eins.config["DB_PATH"]) as conn:
        assert users.gesperrt(conn, "admin")
        # alte Eintraege fallen weg, auch fuer erfundene Namen
        conn.execute("INSERT INTO login_sperre (name, fehl, bis, zuletzt) VALUES ('erfunden', 1, 0, 0)")
        users.fehlversuch(conn, "noch-einer", 5, 300)
        namen = {r[0] for r in conn.execute("SELECT name FROM login_sperre")}
        assert "erfunden" not in namen and {"admin", "noch-einer"} <= namen


def test_totp_code_nur_einmal(tmp_path):
    app = _app(tmp_path)
    geheim = pyotp.random_base32()
    with db.session(app.config["DB_PATH"]) as conn:
        users.set_totp(conn, "admin", geheim)
    # Zeitschritt mit einer Stufe Toleranz
    t = 1_800_000_000
    code = pyotp.TOTP(geheim).at(t)
    assert twofa.totp_schritt(geheim, code, jetzt=t) == t // 30
    assert twofa.totp_schritt(geheim, code, jetzt=t + 30) == t // 30
    assert twofa.totp_schritt(geheim, code, jetzt=t + 90) is None
    assert twofa.totp_schritt(geheim, "abc", jetzt=t) is None

    def anmelden(code):
        c = app.test_client()
        login(c)
        return c.post("/login/2fa", data={"code": code}, follow_redirects=True).get_data(as_text=True)

    code = pyotp.TOTP(geheim).now()
    assert "Der Code stimmt nicht" not in anmelden(code)
    assert "Der Code stimmt nicht" in anmelden(code)          # derselbe Code ein zweites Mal


def test_foto_nur_aus_dem_internet(tmp_path, monkeypatch):
    adressen = {"bilder.example": "93.184.216.34", "intern.example": "192.168.1.10"}

    def dns(host, port, *a, **kw):
        if host not in adressen:
            raise socket.gaierror("unbekannt")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (adressen[host], port))]

    monkeypatch.setattr(bestellungen.socket, "getaddrinfo", dns)
    assert bestellungen._oeffentlich("https://bilder.example/a.jpg")
    for url in ("http://bilder.example/a.jpg", "https://intern.example/a.jpg", "https://127.0.0.1/a.jpg",
                "https://10.0.0.5/a.jpg", "https://[::1]/a.jpg", "https://gibts.nicht/a.jpg", "ftp://x/a"):
        assert not bestellungen._oeffentlich(url), url
    abrufe = []

    def get(url, **kw):
        abrufe.append(url)
        assert kw["allow_redirects"] is False and kw["stream"] is True
        if url.endswith("/umweg.jpg"):
            return _Antwort(status=302, location="https://bilder.example/a.jpg")
        if url.endswith("/nach-innen.jpg"):
            return _Antwort(status=302, location="https://intern.example/geheim")
        if url.endswith("/riesig.jpg"):
            return _Antwort(content=b"x" * (bestellungen.MAX_FOTO_BYTES + 1))
        return _Antwort()

    monkeypatch.setattr(bestellungen.requests, "get", get)
    ordner = str(tmp_path / "f")
    assert bestellungen.lade_foto("https://bilder.example/umweg.jpg", ordner, 1) == "1.jpg"
    assert abrufe == ["https://bilder.example/umweg.jpg", "https://bilder.example/a.jpg"]
    abrufe.clear()
    assert bestellungen.lade_foto("https://bilder.example/nach-innen.jpg", ordner, 2) is None
    assert abrufe == ["https://bilder.example/nach-innen.jpg"]                # intern nie angefragt
    assert bestellungen.lade_foto("https://bilder.example/riesig.jpg", ordner, 3) is None
    assert bestellungen.lade_foto("https://intern.example/a.jpg", ordner, 4) is None
    assert not os.path.exists(os.path.join(ordner, "3.jpg"))


def test_zip_bombe(tmp_path, monkeypatch):
    zip_pfad = tmp_path / "s.zip"
    with zipfile.ZipFile(zip_pfad, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("foxtrail.db", b"\0" * 50_000)                     # packt auf wenige Bytes
    monkeypatch.setattr(config, "max_sicherung", lambda: 10_000)
    with pytest.raises(sicherung.SicherungFehler, match="zu gross"):
        sicherung.einlesen(str(zip_pfad), str(tmp_path / "t.db"), str(tmp_path / "f"))
    assert not os.path.exists(tmp_path / "t.db")
