# -*- coding: utf-8 -*-
"""End-to-End ueber den Flask-Testclient (temporaere SQLite-Datei)."""

import datetime
import io
import json
import os
import sys
import time

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
    assert 'class="kacheln"' in c.get("/").get_data(as_text=True)      # Standard: Kacheln
    c.get("/?ansicht=liste")                                           # Wahl bleibt in der Session
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
    c.post("/trail/2", data={"gemacht": "1", "gemacht_datum": "2024-07-01", "next": "/archiv"})
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
    assert "gelöscht" in r.get_data(as_text=True)
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
    assert "gelöscht" in r.get_data(as_text=True)


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
    c.get("/?ansicht=liste")
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
    assert c.get("/import").status_code == 403                      # nur Admins
    assert c.post("/import", data={"schritt": "schreiben", "daten": "[]"}).status_code == 403
    c.get("/logout")
    login(c, "admin")
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
    assert "Ungültige Daten" in r.get_data(as_text=True)
    c.get("/logout")
    assert c.get("/foto/1").status_code == 302                    # nur angemeldet


def _jpeg_bytes(breite=1200, hoehe=800):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (breite, hoehe), (40, 90, 60)).save(out, "JPEG")
    return out.getvalue()


def test_foto_hochladen_ersetzen_loeschen(app, tmp_path):
    from PIL import Image
    ordner = tmp_path / "fotos"
    app.config["FOTO_DIR"] = str(ordner)
    c = app.test_client()
    login(c, "gast")
    assert "Foto hinzufügen" in c.get("/trail/1").get_data(as_text=True)
    # kein Bild -> abgelehnt, nichts gespeichert
    r = c.post("/trail/1/foto", data={"aktion": "hochladen", "datei": (io.BytesIO(b"kein bild"), "x.jpg")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "Nur JPEG" in r.get_data(as_text=True)
    # grosses Bild -> verkleinert auf 2560 px
    r = c.post("/trail/1/foto", data={"aktion": "hochladen", "datei": (io.BytesIO(_jpeg_bytes(4000, 3000)), "a.jpg")},
               content_type="multipart/form-data", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Foto gespeichert" in html and "Foto ersetzen" in html and "Foto löschen" in html
    with db.session(app.config["DB_PATH"]) as conn:
        erstes = trails.get(conn, 1)["foto"]
    assert erstes.startswith("1-") and Image.open(ordner / erstes).size == (2560, 1920)
    # offener Trail: Foto allein macht ihn nicht "gemacht" (Datum ist Pflicht), der Haken ist
    # aber vorgesetzt und das Formular bittet ums Datum
    assert "Bitte noch das Datum eintragen" in html and 'name="gemacht" value="1" checked' in html
    with db.session(app.config["DB_PATH"]) as conn:
        assert trails.get(conn, 1)["gemacht"] == 0
    # Vorschau fuer Liste/Kacheln
    r = c.get(f"/foto/1?g=klein&v={erstes}")
    assert r.status_code == 200 and Image.open(io.BytesIO(r.data)).size[0] == 640
    r.close()                                                 # Windows loescht keine offenen Dateien
    assert (ordner / "klein" / (erstes[:-4] + ".jpg")).exists()
    assert f"/foto/1?g=klein" in c.get("/?ansicht=liste").get_data(as_text=True)
    # ersetzen -> alte Datei samt Vorschau weg
    import time
    time.sleep(1.1)                                           # neuer Zeitstempel im Namen
    c.post("/trail/1/foto", data={"aktion": "hochladen", "datei": (io.BytesIO(_jpeg_bytes()), "b.jpg")},
           content_type="multipart/form-data")
    with db.session(app.config["DB_PATH"]) as conn:
        zweites = trails.get(conn, 1)["foto"]
    assert zweites != erstes and not (ordner / erstes).exists() and (ordner / zweites).exists()
    assert not (ordner / "klein" / (erstes[:-4] + ".jpg")).exists()
    # loeschen -> '' (Import laedt es nicht wieder)
    r = c.post("/trail/1/foto", data={"aktion": "loeschen"}, follow_redirects=True)
    assert "Foto gelöscht" in r.get_data(as_text=True)
    with db.session(app.config["DB_PATH"]) as conn:
        assert trails.get(conn, 1)["foto"] == ""
    assert not (ordner / zweites).exists() and c.get("/foto/1").status_code == 404
    c.get("/logout")
    assert c.post("/trail/1/foto", data={"aktion": "loeschen"}).status_code == 302   # nur angemeldet


def test_import_laedt_geloeschtes_foto_nicht_neu(app, monkeypatch, tmp_path):
    from foxtrail import bestellungen
    from tests.test_bestellungen import MUSTER_JSON, _Antwort
    geladen = []
    monkeypatch.setattr(bestellungen.requests, "get",
                        lambda url, timeout=None, headers=None: geladen.append(url) or _Antwort())
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("UPDATE trails SET name = 'Columban', foto = '' WHERE slug = 'aargau/aquae'")
        plan = bestellungen.zuordnen(conn, bestellungen.parse(json.dumps(MUSTER_JSON)))
        res = bestellungen.anwenden(conn, plan, "test", str(tmp_path))
        assert res["gesetzt"] == 1 and res["fotos"] == 0 and not geladen
        assert trails.get(conn, 1)["foto"] == "" and trails.get(conn, 1)["foto_url"]
    # von Hand wieder holen geht
    app.config["FOTO_DIR"] = str(tmp_path)
    c = app.test_client()
    login(c, "gast")
    assert "Schlussfoto von foxtrail.ch laden" in c.get("/trail/1").get_data(as_text=True)
    r = c.post("/trail/1/foto", data={"aktion": "foxtrail"}, follow_redirects=True)
    assert "Schlussfoto von foxtrail.ch geladen" in r.get_data(as_text=True) and geladen


def test_kachelansicht_und_titelbild(app, monkeypatch, tmp_path):
    from foxtrail import fotos

    class Bild:
        content = _jpeg_bytes(441, 294)
        def raise_for_status(self):
            pass
    abrufe = []
    monkeypatch.setattr(fotos.requests, "get", lambda url, timeout=None, headers=None: abrufe.append(url) or Bild())
    app.config["FOTO_DIR"] = str(tmp_path)
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("UPDATE trails SET bild_url = 'https://foxtrail.ch/wp-content/uploads/a.jpg' WHERE id = 1")
        conn.execute("UPDATE trails SET bild_url = 'https://boese.example/x.jpg' WHERE id = 2")
    c = app.test_client()
    login(c, "gast")
    html = c.get("/?ansicht=kacheln").get_data(as_text=True)
    assert 'class="kacheln"' in html and "/titelbild/1" in html and "<table" not in html
    assert 'class="kfox"' in html and "foxtrail.ch ↗" in html        # Direktlink zu foxtrail.ch
    assert 'onerror="this.remove()"' in html and "Brig" in html   # Ort bleibt sichtbar, wenn das Bild fehlt
    # die Wahl bleibt in der Sitzung
    assert 'class="kacheln"' in c.get("/?sort=name").get_data(as_text=True)
    assert "<table" in c.get("/?ansicht=liste").get_data(as_text=True)
    assert "<table" in c.get("/").get_data(as_text=True)
    # Titelbild: einmal laden, danach aus dem Zwischenspeicher
    assert c.get("/titelbild/1").status_code == 200 and c.get("/titelbild/1").status_code == 200
    assert abrufe == ["https://foxtrail.ch/wp-content/uploads/a.jpg"]
    assert c.get("/titelbild/2").status_code == 404            # nur Bilder von foxtrail.ch
    assert c.get("/titelbild/99").status_code == 404


def test_statistik(app):
    c = app.test_client()
    login(c, "gast")
    assert "Noch kein Trail" in c.get("/statistik").get_data(as_text=True)
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("UPDATE trails SET gemacht = 1, gemacht_datum = '2023-05-01', mitspieler = 4, "
                     "start_zeit = '2023-05-01 10:00', ziel_zeit = '2023-05-01 12:30' WHERE id = 1")
        conn.execute("UPDATE trails SET gemacht = 1, gemacht_datum = '2025-06-01', mitspieler = 2, "
                     "start_zeit = '2025-06-01 10:00', ziel_zeit = '2025-06-01 13:10' WHERE id = 2")
        st = trails.statistik(conn)
    assert st["gemacht"] == 2 and st["regionen_besucht"] == 2 and st["regionen_gesamt"] == 2
    assert st["top_region"][1] in ("Aargau", "Wallis") and st["dieses_jahr"] == (1 if st["jahr"] in (2023, 2025) else 0)
    assert st["jahre"] == [(2023, 1), (2024, 0), (2025, 1)]              # Luecke als 0
    assert st["spielzeit_summe"] == "5:40 h" and st["spielzeit_schnitt"] == "2:50 h"
    assert st["schnellster"]["name"] == "Aquae" and st["laengster"]["name"] == "Simplon"
    assert ("aargau", "Aargau", 1, 1) in st["regionen"]
    html = c.get("/statistik").get_data(as_text=True)
    assert "5:40 h" in html and "Regionen erkundet" in html and "Mitspieler gesamt" not in html
    assert "Schnellster" in html and "Aargau" in html and "2024" in html
    c.get("/logout")
    assert c.get("/statistik").status_code == 302


def test_handy_markup(app):
    """Die Handy-Darstellung haengt an Klassen im HTML - hier pruefen, dass sie da sind."""
    c = app.test_client()
    login(c, "gast")
    html = c.get("/?ansicht=liste").get_data(as_text=True)
    assert "pw-auge" in html                                    # Auge fuer Passwortfelder
    assert 'class="navtoggle"' in html and 'id="hauptnav"' in html and 'name="theme-color"' in html
    assert 'class="trails karten"' in html and 'class="m-name"' in html and 'class="k-region"' in html
    assert 'class="trails karten ohne-haken"' in c.get("/archiv").get_data(as_text=True)


def test_menue(app):
    c = app.test_client()
    login(c, "gast")
    html = c.get("/").get_data(as_text=True)
    assert 'class="dd"' in html and "Archiv" in html and "Trail manuell erfassen" in html
    assert "Passwort ändern" in html and "Abmelden" in html
    assert "/import" not in html and "/admin/sync" not in html and "/admin/benutzer" not in html  # nur Admins
    c.get("/logout")
    login(c, "admin")
    html = c.get("/archiv").get_data(as_text=True)
    assert "/import" in html and "/admin/sync" in html and "/admin/benutzer" in html and "(Admin)" in html


def test_spalten_pro_benutzer(app):
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("UPDATE trails SET route = 'Bahnhof - Altstadt - Bäder', team_code = 'QWERTZ', "
                     "start_zeit = '2025-06-01 10:05', ziel_zeit = '2025-06-01 12:40' WHERE id = 1")
    c = app.test_client()
    login(c, "gast")
    html = c.get("/?ansicht=liste").get_data(as_text=True)
    assert "Bemerkung</th>" in html and "Team-Code</th>" not in html          # Standard
    assert 'name="spalte" value="team"' in html and 'form="spalten-form"' in html
    assert "Standardspalten wiederherstellen" in html and "sp-mark" not in html   # Standard aktiv
    # nur Typ, Startort, Zielort, Startzeit, Team-Code; unbekannte Schluessel werden ignoriert
    r = c.post("/spalten", data={"spalte": ["typ", "startort", "zielort", "start", "team", "unsinn"],
                                 "next": "/?ansicht=liste"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Spaltenauswahl gespeichert" in html and "sp-mark" in html and "eigene Auswahl" in html
    assert "Team-Code</th>" in html and "QWERTZ" in html and ">Bahnhof<" in html and ">Bäder<" in html
    assert ">10:05<" in html and "Bemerkung</th>" not in html and "Region" not in html.split("<tbody>")[0].split("<thead>")[1]
    assert "/?ansicht=liste&amp;sort=startort" in html or "sort=startort" in html
    with db.session(app.config["DB_PATH"]) as conn:
        assert users.get(conn, "gast")["spalten"] == "startort,zielort,typ,start,team"   # Anzeigereihenfolge
        assert users.get(conn, "admin")["spalten"] is None                    # andere unberuehrt
    assert c.get("/?sort=startort").status_code == 200
    # alles abwaehlen -> nur Ort und Trail
    c.post("/spalten", data={"next": "/"})
    thead = c.get("/?ansicht=liste").get_data(as_text=True).split("<thead>")[1].split("</thead>")[0]
    assert thead.count("<th") == 3
    # Standard
    r = c.post("/spalten", data={"aktion": "standard", "next": "//boese.example"}, follow_redirects=True)
    assert "Standard zurückgesetzt" in r.get_data(as_text=True) and "Bemerkung</th>" in r.get_data(as_text=True)
    c.get("/logout")
    assert c.post("/spalten", data={"spalte": "typ"}).status_code == 302   # nur angemeldet


def test_timer_status():
    ausgabe = ("LoadState=loaded\nActiveState=active\n"
               "TimersCalendar={ OnCalendar=Mon *-*-* 04:30:00 ; next_elapse=Mon 2026-09-21 04:30:00 CEST }\n"
               "NextElapseUSecRealtime=Mon 2026-09-21 04:30:00 CEST\nLastTriggerUSec=Mon 2026-09-14 04:41:03 CEST\n"
               "RandomizedDelayUSec=30min\n")
    t = sync.timer_status(ausgabe)
    assert t == {"aktiv": True, "plan": "jeden Montag um 04:30", "naechster": "Montag, 21.09.2026, 04:30",
                 "letzter": "Montag, 14.09.2026, 04:41", "verzoegerung": "30 Min."}
    assert sync.timer_status("LoadState=not-found\nActiveState=inactive\n") is None
    assert sync.timer_status("")  is None
    assert sync.plan_text("*-*-* 03:00:00") == "*-*-* 03:00:00"          # Unbekanntes bleibt


def test_sync_seite_zeigt_zeitplan(app, monkeypatch):
    c = app.test_client()
    login(c, "admin")
    monkeypatch.setattr(sync, "timer_status", lambda: {"aktiv": True, "plan": "jeden Montag um 04:30",
                        "naechster": "Montag, 21.09.2026, 04:30", "letzter": "", "verzoegerung": "30min"})
    html = c.get("/admin/sync").get_data(as_text=True)
    assert "Nächster Lauf" in html and "Montag, 21.09.2026, 04:30" in html and "jeden Montag um 04:30" in html
    monkeypatch.setattr(sync, "timer_status", lambda: None)
    assert "Montag um 04:30" in c.get("/admin/sync").get_data(as_text=True)   # Hinweis ohne systemd


def test_zeitplan_docker(tmp_path):
    from foxtrail import zeitplan
    dt = datetime.datetime
    mo = dt(2026, 9, 21, 4, 30)                                   # Montag
    assert zeitplan.letzter_termin(dt(2026, 9, 23, 12, 0)) == mo
    assert zeitplan.letzter_termin(dt(2026, 9, 21, 4, 29)) == dt(2026, 9, 14, 4, 30)
    assert zeitplan.letzter_termin(mo) == mo
    assert zeitplan.naechster_termin(dt(2026, 9, 21, 4, 31)) == dt(2026, 9, 28, 4, 30)
    # verpasst: seit dem letzten Montagstermin kein Timer-Lauf; Neuinstallation nicht
    assert not zeitplan.faellig(None, dt(2026, 9, 23, 12, 0))
    assert zeitplan.faellig("2026-09-14 04:41:00", dt(2026, 9, 23, 12, 0))
    assert not zeitplan.faellig("2026-09-21 04:41:00", dt(2026, 9, 23, 12, 0))
    # Status fuer die Seite Abgleich aus zeitplan.json
    pfad = str(tmp_path / "zeitplan.json")
    assert zeitplan.status(pfad) is None
    zeitplan._schreiben(pfad, dt(2026, 9, 28, 4, 47), dt(2026, 9, 23, 12, 0))
    st = zeitplan.status(pfad, jetzt=dt(2026, 9, 23, 12, 10))
    # angezeigt wird der regulaere Termin, die Zufallsverzoegerung als Hinweis (wie systemd)
    assert st["aktiv"] and st["docker"] and st["naechster"] == "Montag, 28.09.2026, 04:30"
    assert st["verzoegerung"] == "30 Min."
    assert not zeitplan.status(pfad, jetzt=dt(2026, 9, 23, 14, 0))["aktiv"]      # Herzschlag veraltet
    zeitplan._schreiben(pfad, dt(2026, 9, 23, 12, 1), dt(2026, 9, 23, 12, 0), nachholen=True)
    assert zeitplan.status(pfad, jetzt=dt(2026, 9, 23, 12, 0))["naechster"] == \
        "Mittwoch, 23.09.2026, 12:01 (verpasster Termin wird nachgeholt)"


def test_sync_seite_docker(app, monkeypatch):
    from foxtrail import zeitplan
    c = app.test_client()
    login(c, "admin")
    monkeypatch.setattr(sync, "timer_status", lambda: None)
    zeitplan._schreiben(zeitplan.datei(app.config["DB_PATH"]), datetime.datetime(2026, 9, 28, 4, 47),
                        datetime.datetime.now())
    html = c.get("/admin/sync").get_data(as_text=True)
    assert "eingeschaltet" in html and "Montag, 28.09.2026, 04:30" in html and "bis zu 30 Min." in html
    zeitplan._schreiben(zeitplan.datei(app.config["DB_PATH"]), datetime.datetime(2026, 9, 28, 4, 47),
                        datetime.datetime(2026, 1, 1))
    assert "läuft nicht" in c.get("/admin/sync").get_data(as_text=True)


def test_ueber_und_update_hinweis(app, tmp_path):
    from foxtrail import version
    assert version.neuer("1.10.0", "1.9.3") and version.neuer("v2.0", "1.3.0")
    assert not version.neuer("1.3.0", "1.3.0") and not version.neuer("kaputt", "1.3.0")
    assert version.stand(str(tmp_path)) == version.VERSION
    (tmp_path / "DEV_STAND").write_text("main@abc1234\n")
    assert version.stand(str(tmp_path)) == f"{version.VERSION} (Entwicklungsstand main@abc1234)"

    c = app.test_client()
    login(c, "admin")
    html = c.get("/").get_data(as_text=True)
    assert f"Version {version.VERSION}" in html and version.REPO_URL in html and "upd-punkt" not in html
    # Zwischenspeicher mit neuerer Version -> Hinweis fuer Admins, nicht fuer andere
    with open(version.datei(app.config["DB_PATH"]), "w") as fh:
        json.dump({"geprueft": time.time(), "version": "99.0.0", "url": "https://example.org/rel"}, fh)
    html = c.get("/").get_data(as_text=True)
    assert "upd-punkt" in html and "Neue Version 99.0.0 verfügbar" in html
    c2 = app.test_client()
    login(c2, "gast")
    html = c2.get("/").get_data(as_text=True)
    assert "upd-punkt" not in html and f"Version {version.VERSION}" in html


def test_archiv_suche_und_sortierung(app):
    c = app.test_client()
    login(c)
    with db.session(app.config["DB_PATH"]) as conn:
        sync.apply(conn, [mk("aargau/aquae", ort="Baden", name="Aquae")])
        for slug, ort, name, region, gesehen in (("bern/zett", "Bern", "Zett", "bern-und-umgebung", "2024-01-01"),
                                                 ("wallis/alpha", "Zinal", "Alpha", "wallis", "2025-06-01"),
                                                 ("aargau/mitte", "Aarau", "Mitte", "aargau", "2023-05-01")):
            conn.execute("INSERT INTO trails (slug, quelle, ort, name, route, region, im_angebot, first_seen, "
                         "last_seen) VALUES (?, 'foxtrail', ?, ?, 'Altstadt - See', ?, 0, ?, ?)",
                         (slug, ort, name, region, gesehen, gesehen))

    def namen(url):
        """Reihenfolge der Test-Trails auf der Seite."""
        html = c.get(url).get_data(as_text=True)
        return [n for _, n in sorted((html.index(f"<b>{n}</b>"), n) for n in ("Zett", "Alpha", "Mitte")
                                     if f"<b>{n}</b>" in html)]

    assert namen("/archiv") == ["Mitte", "Zett", "Alpha"]                                  # nach Ort
    assert namen("/archiv?sort=name") == ["Alpha", "Mitte", "Zett"]
    assert namen("/archiv?sort=region&dir=desc") == ["Alpha", "Zett", "Mitte"]
    assert namen("/archiv?sort=gesehen&dir=desc") == ["Alpha", "Zett", "Mitte"]
    assert namen("/archiv?q=zin") == ["Alpha"]
    html = c.get("/archiv?q=xyz").get_data(as_text=True)
    assert "Keine Treffer." in html and "Suche zurücksetzen" in html
    assert "sort=name" in c.get("/archiv").get_data(as_text=True)                          # Sortier-Links


def test_seiten_nicht_zwischengespeichert(app):
    c = app.test_client()
    login(c)
    assert c.get("/archiv").headers["Cache-Control"] == "no-store"
    assert c.get("/login").headers["Cache-Control"] == "no-store"
    assert "no-store" not in (c.get("/static/style.css").headers.get("Cache-Control") or "")


def test_datum_ohne_haken_zaehlt_als_gemacht(app):
    c = app.test_client()
    login(c)
    r = c.post("/trail/2", data={"gemacht_datum": "2025-04-12", "mitspieler": "2", "next": "/"},
               follow_redirects=True)
    assert "als gemacht markiert" in r.get_data(as_text=True)
    with db.session(app.config["DB_PATH"]) as conn:
        t = trails.get(conn, 2)
    assert (t["gemacht"], t["gemacht_datum"], t["mitspieler"]) == (1, "2025-04-12", 2)
    # schon gemacht: Haken entfernen setzt zurueck, auch wenn das Datum noch im Formular steht
    r = c.post("/trail/2", data={"gemacht_datum": "2025-04-12", "mitspieler": "2", "next": "/"},
               follow_redirects=True)
    with db.session(app.config["DB_PATH"]) as conn:
        assert trails.get(conn, 2)["gemacht"] == 0
    # nur Bemerkung: bleibt offen
    c.post("/trail/2", data={"bemerkung": "irgendwann", "next": "/"})
    with db.session(app.config["DB_PATH"]) as conn:
        assert trails.get(conn, 2)["gemacht"] == 0


def test_gemacht_nur_mit_datum(app):
    c = app.test_client()
    login(c)
    # Haken ohne Datum: nicht gespeichert, Fehlermeldung, Eingaben bleiben stehen
    r = c.post("/trail/2", data={"gemacht": "1", "mitspieler": "3", "bemerkung": "toll", "next": "/"},
               follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Bitte das Datum eintragen" in html and "toll" in html
    with db.session(app.config["DB_PATH"]) as conn:
        t = trails.get(conn, 2)
    assert t["gemacht"] == 0 and not t["bemerkung"]
    # nur Mitspieler (zaehlt als gemacht) ohne Datum: ebenfalls abgelehnt
    r = c.post("/trail/2", data={"mitspieler": "3", "next": "/"}, follow_redirects=True)
    assert "Bitte das Datum eintragen" in r.get_data(as_text=True)
    # manueller Trail "gemacht" ohne Datum: nicht angelegt
    r = c.post("/trail/neu", data={"ort": "Brienz", "name": "Ohne Datum", "gemacht": "1"},
               follow_redirects=True)
    assert "Bitte das Datum eintragen" in r.get_data(as_text=True)
    with db.session(app.config["DB_PATH"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trails WHERE name = 'Ohne Datum'").fetchone()[0] == 0
    # Formular: Datum ist Pflicht, sobald der Haken gesetzt ist (Hinweis + JS)
    assert "Pflicht, wenn gemacht" in c.get("/trail/2").get_data(as_text=True)


def test_healthz(app):
    assert app.test_client().get("/healthz").data == b"ok"
