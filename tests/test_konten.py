# -*- coding: utf-8 -*-
"""Benutzerverwaltung: Passwort-Optionen, Zweitfaktor, Anmelde-Protokoll."""

import os
import sys

import pyotp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxtrail import authlog, db, users  # noqa: E402
from tests.test_app import app, login  # noqa: E402,F401  (Fixture)


def _neu(c, **extra):
    daten = {"modus": "neu", "username": "tanja", "password": "start-1234", "rolle": "bearbeiten",
             "pw_modus": "frei", "aktiv": "1"}
    daten.update(extra)
    return c.post("/admin/benutzer/speichern", data=daten, follow_redirects=True)


def _u(app, name="tanja"):
    with db.session(app.config["DB_PATH"]) as conn:
        return users.get(conn, name)


def test_anlegen_mit_optionen_und_bearbeiten(app):
    c = app.test_client()
    login(c)
    r = _neu(c, anzeigename="Tanja Z.", pw_modus="wechsel", twofa_pflicht="1", sprache="fr")
    assert "Benutzer „tanja“ angelegt." in r.get_data(as_text=True)
    u = _u(app)
    assert (u["anzeigename"], u["pw_wechsel"], u["pw_fest"], u["twofa_pflicht"], u["sprache"]) == \
        ("Tanja Z.", 1, 0, 1, "fr")
    html = c.get("/admin/benutzer?edit=tanja").get_data(as_text=True)
    assert "Benutzer bearbeiten: tanja" in html and "Tanja Z." in html and "Wechsel offen" in html
    # bearbeiten: Passwort gesperrt, 2FA-Pflicht weg, neues Passwort
    c.post("/admin/benutzer/speichern", data={"modus": "bearbeiten", "username": "tanja", "rolle": "lesen",
                                              "pw_modus": "fest", "aktiv": "1", "password": "anders-123"})
    u = _u(app)
    assert (u["pw_wechsel"], u["pw_fest"], u["twofa_pflicht"], u["nur_lesen"]) == (0, 1, 0, 1)
    assert login(app.test_client(), "tanja", "anders-123").status_code == 200
    # nicht selbst deaktivieren
    r = c.post("/admin/benutzer/speichern", data={"modus": "bearbeiten", "username": "admin", "rolle": "admin",
                                                  "pw_modus": "frei"}, follow_redirects=True)
    assert "nicht selbst deaktivieren" in r.get_data(as_text=True) and _u(app, "admin")["active"] == 1


def test_passwort_wechsel_erzwungen(app):
    c = app.test_client()
    login(c)
    _neu(c, pw_modus="wechsel")
    t = app.test_client()
    r = login(t, "tanja", "start-1234")
    assert "Bevor es weitergeht" in r.get_data(as_text=True)
    assert t.get("/").status_code == 302 and t.get("/statistik").headers["Location"].endswith("/passwort")
    r = t.post("/passwort", data={"old": "start-1234", "new": "start-1234", "new2": "start-1234"},
               follow_redirects=True)
    assert "Grossbuchstaben" in r.get_data(as_text=True)                  # Regeln greifen
    r = t.post("/passwort", data={"old": "start-1234", "new": "Neu!Pass9", "new2": "Neu!Pass9"},
               follow_redirects=True)
    assert "Passwort geändert." in r.get_data(as_text=True) and _u(app)["pw_wechsel"] == 0
    assert t.get("/").status_code == 200


def test_passwort_darf_nicht_geaendert_werden(app):
    c = app.test_client()
    login(c)
    _neu(c, pw_modus="fest")
    t = app.test_client()
    login(t, "tanja", "start-1234")
    assert "kann das Passwort nicht geändert werden" in t.get("/profil").get_data(as_text=True)
    r = t.post("/passwort", data={"old": "start-1234", "new": "Neu!Pass9", "new2": "Neu!Pass9"},
               follow_redirects=True)
    assert "kann das Passwort nicht geändert werden" in r.get_data(as_text=True)
    assert login(app.test_client(), "tanja", "start-1234").status_code == 200   # altes gilt weiter
    # beides zugleich geht nicht
    with db.session(app.config["DB_PATH"]) as conn:
        try:
            users.aendern(conn, "tanja", pw_wechsel=True, pw_fest=True)
            assert False, "hätte scheitern müssen"
        except users.UserError as ex:
            assert "schliessen sich aus" in str(ex)


def test_totp_einrichten_und_anmelden(app):
    c = app.test_client()
    login(c, "gast")
    html = c.get("/2fa/einrichten").get_data(as_text=True)
    assert "<svg" in html and "Authenticator-App aktivieren" in html
    with c.session_transaction() as s:
        secret = s["totp_neu"]
    r = c.post("/2fa/einrichten", data={"code": "000000"}, follow_redirects=True)
    assert "Der Code stimmt nicht" in r.get_data(as_text=True)
    r = c.post("/2fa/einrichten", data={"code": pyotp.TOTP(secret).now()}, follow_redirects=True)
    assert "Authenticator-App eingerichtet." in r.get_data(as_text=True)
    assert _u(app, "gast")["totp_secret"] == secret
    # neue Anmeldung: nach dem Passwort kommt der Code
    t = app.test_client()
    r = login(t, "gast")
    assert "Code aus der Authenticator-App" in r.get_data(as_text=True)
    assert t.get("/").status_code == 302                                   # noch nicht angemeldet
    r = t.post("/login/2fa", data={"code": "123456"}, follow_redirects=True)
    assert "Der Code stimmt nicht." in r.get_data(as_text=True)
    t.post("/login/2fa", data={"code": pyotp.TOTP(secret).now()})
    assert t.get("/").status_code == 200
    # Profil: entfernen
    c.post("/profil/totp-entfernen")
    assert _u(app, "gast")["totp_secret"] is None


def test_2fa_pflicht_erzwingt_einrichtung_und_admin_setzt_zurueck(app):
    c = app.test_client()
    login(c)
    _neu(c, twofa_pflicht="1")
    t = app.test_client()
    r = login(t, "tanja", "start-1234")
    assert "Zweitfaktor einrichten" in r.get_data(as_text=True)
    assert t.get("/").status_code == 302                                   # erst nach der Einrichtung
    with t.session_transaction() as s:
        secret = s["totp_neu"]
    t.post("/2fa/einrichten", data={"code": pyotp.TOTP(secret).now()})
    assert t.get("/").status_code == 200
    # Passkey-Optionen fuer die Anmeldung gibt es nur mit offenem Login
    assert app.test_client().get("/2fa/passkey/auth-options").status_code == 403
    # Admin setzt den Zweitfaktor zurueck -> beim naechsten Login wieder einrichten
    r = c.post("/admin/benutzer/tanja/2fa-zuruecksetzen", follow_redirects=True)
    assert "Zweitfaktor von „tanja“ zurückgesetzt." in r.get_data(as_text=True)
    assert not users.has_2fa(_u(app))
    assert "Zweitfaktor einrichten" in login(app.test_client(), "tanja", "start-1234").get_data(as_text=True)


def test_passkey_optionen(app):
    c = app.test_client()
    login(c, "gast")
    r = c.get("/2fa/passkey/register-options", headers={"Host": "localhost:8080"})
    assert r.status_code == 200 and r.json["rp"]["id"] == "localhost" and r.json["user"]["name"] == "gast"
    # ohne HTTPS und mit IP: nur TOTP
    assert "Passkey registrieren" not in c.get("/profil", headers={"Host": "10.0.1.5:8080"}).get_data(as_text=True)
    assert "Passkey registrieren" in c.get("/profil", headers={"Host": "localhost:8080"}).get_data(as_text=True)
    # Verifizieren mit Unsinn scheitert sauber
    r = c.post("/2fa/passkey/register-verify", data="{}", headers={"Host": "localhost:8080"})
    assert r.status_code == 400 and r.json["error"]


def test_protokoll(app):
    c = app.test_client()
    login(c, pw="falsch")
    login(c)
    _neu(c)
    c.post("/admin/benutzer/tanja/loeschen")
    login(app.test_client(), "gast").get_data()
    app.test_client().get("/")                                              # kein Eintrag
    g = app.test_client()
    login(g, "gast")
    g.get("/admin/benutzer")                                                # verweigert
    with db.session(app.config["DB_PATH"]) as conn:
        rows = authlog.lesen(conn)
    was = [(r["benutzer"], r["ereignis"]) for r in reversed(rows)]
    assert ("admin", "fail") in was and ("admin", "login") in was and ("admin", "user_add") in was
    assert ("admin", "user_del") in was and ("gast", "denied") in was
    html = c.get("/admin/protokoll?ereignis=user_add").get_data(as_text=True)
    assert "Benutzer angelegt" in html and "tanja" in html and "Fehlversuch (Passwort)" not in html.split("<tbody>")[1]
    assert g.get("/admin/protokoll").status_code == 403


def test_anmelden_mit_passkey_statt_passwort(app):
    c = app.test_client()
    lokal, ip = {"Host": "localhost:8080"}, {"Host": "10.0.1.5:8080"}
    # ohne hinterlegten Passkey wird die Anmeldung mit Passkey nicht angeboten
    assert "Mit Passkey anmelden" not in c.get("/login", headers=lokal).get_data(as_text=True)
    with db.session(app.config["DB_PATH"]) as conn:
        users.add_passkey(conn, "gast", {"id": "cred-1", "public_key": "xx",
                                         "sign_count": 0, "name": "Handy",
                                         "created": "2026-09-20"})
    assert "Mit Passkey anmelden" in c.get("/login", headers=lokal).get_data(as_text=True)
    assert "Mit Passkey anmelden" not in c.get("/login", headers=ip).get_data(as_text=True)
    # Anfrage ohne allowCredentials: der Browser sucht den Passkey selbst
    r = c.get("/login/passkey/options", headers=lokal)
    assert r.status_code == 200 and not r.json.get("allowCredentials")
    assert c.get("/login/passkey/options", headers=ip).status_code == 400
    # unbekannter Passkey: saubere Meldung, niemand ist angemeldet
    r = c.post("/login/passkey/verify", data='{"id": "fremd", "response": {}}', headers=lokal)
    assert r.status_code == 400 and "keinem Konto" in r.json["error"]
    assert c.get("/").status_code == 302
    # Zuordnung: ueber das user handle und ueber die Credential-ID allein
    with db.session(app.config["DB_PATH"]) as conn:
        assert users.by_passkey(conn, "cred-1", "gast")[0]["username"] == "gast"
        assert users.by_passkey(conn, "cred-1", "niemand")[0]["username"] == "gast"
        assert users.by_passkey(conn, "cred-1")[1]["name"] == "Handy"
        assert users.by_passkey(conn, "anderer") == (None, None)
        users.aendern(conn, "gast", aktiv=False)
        assert users.by_passkey(conn, "cred-1") == (None, None)      # gesperrtes Konto zaehlt nicht


def test_darstellung_und_menue(app):
    c = app.test_client()
    html = c.get("/login").get_data(as_text=True)
    assert 'data-theme="auto"' in html and 'id="themebtn"' in html            # auch abgemeldet
    login(c)
    html = c.get("/").get_data(as_text=True)
    assert "Anmelde-Protokoll" in html and 'id="themebtn"' in html and "/profil" in html
    # Schublade fuers Smartphone: Schatten zum Schliessen und Kopf mit Schliessen-Knopf
    assert 'id="navoverlay"' in html and "navclose" in html and "Menü schliessen" in html
