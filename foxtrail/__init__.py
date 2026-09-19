# -*- coding: utf-8 -*-
"""
Foxtrail-Tracker - Flask-App.

Login nach dem Muster eines Session-Logins: Benutzer in SQLite mit gehashtem
Passwort, serverseitig signierte Session (reines Sitzungscookie), Sperre nach
zu vielen Fehlversuchen (In-Memory). Kein 2FA, keine Rollen ausser dem Flag
"Administrator" (Benutzerverwaltung + Abgleich ausloesen).
"""

import functools
import json
import time

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)

from . import bestellungen, config, db, fotos, sync, trails, users, version, zeitplan

_ATTEMPTS = {}   # username -> [fails, lock_until_ts]


def create_app(test_config=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key()
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.force_https()
    app.config["DB_PATH"] = config.db_path()
    app.config["FOTO_DIR"] = config.foto_dir()
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024     # Handy-Fotos (Upload) und konto.json
    app.config["UPDATE_CHECK"] = config.update_check()
    if test_config:
        app.config.update(test_config)
    if app.testing:
        app.config["UPDATE_CHECK"] = False                  # Tests fragen nie bei GitHub an

    with db.session(app.config["DB_PATH"]):
        pass   # Schema anlegen

    # ---- DB je Request ------------------------------------------------ #
    def get_conn():
        if "conn" not in g:
            g.conn = db.connect(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def _close(exc):
        conn = g.pop("conn", None)
        if conn is not None:
            if exc is None:
                conn.commit()
            else:
                conn.rollback()
            conn.close()

    # ---- Auth ---------------------------------------------------------- #
    def current_user():
        if "me" not in g:
            u = users.get(get_conn(), session.get("user"))
            g.me = u if (u and u["active"]) else None
        return g.me

    def login_required(view):
        @functools.wraps(view)
        def wrapped(*a, **kw):
            if current_user() is None:
                return redirect(url_for("login", next=request.path))
            return view(*a, **kw)
        return wrapped

    def schreiben_required(view):
        """Aendernde Aktionen: nicht fuer Benutzer mit der Rolle "Nur lesen"."""
        @functools.wraps(view)
        def wrapped(*a, **kw):
            me = current_user()
            if me is None:
                return redirect(url_for("login", next=request.path))
            if not users.darf_schreiben(me):
                abort(403)
            return view(*a, **kw)
        return wrapped

    def admin_required(view):
        @functools.wraps(view)
        def wrapped(*a, **kw):
            me = current_user()
            if me is None:
                return redirect(url_for("login", next=request.path))
            if not me["is_admin"]:
                abort(403)
            return view(*a, **kw)
        return wrapped

    def _locked(name):
        rec = _ATTEMPTS.get(name)
        return bool(rec and rec[1] > time.time())

    def _fail(name):
        rec = _ATTEMPTS.setdefault(name, [0, 0])
        rec[0] += 1
        if rec[0] >= config.LOGIN_MAX_FAILS:
            rec[1] = time.time() + config.LOGIN_LOCK_SECONDS
            rec[0] = 0

    @app.template_filter("chf")
    def _chf(v):
        if v in (None, ""):
            return ""
        return f"{v:.2f}".rstrip("0").rstrip(".") if float(v) != int(v) else f"{int(v)}"

    @app.template_filter("dauer")
    def _dauer(v):
        """'1.5-2.5 Stunden' -> '1.5–2.5 h'"""
        return trails.dauer_kurz(v)

    @app.template_filter("mmjj")
    def _mmjj(v):
        """2026-09-14 -> 09/26"""
        return f"{v[5:7]}/{v[2:4]}" if v and len(v) >= 10 else ""

    @app.template_filter("datum_zeit")
    def _datum_zeit(v):
        """2026-09-14 04:41 -> 14.09.2026, 04:41"""
        return f"{_datum(v[:10])}, {v[11:16]}" if v and len(v) >= 16 else _datum(v)

    @app.template_filter("datum")
    def _datum(v):
        """2025-06-01 -> 01.06.2025"""
        if not v or len(v) < 10:
            return v or ""
        return f"{v[8:10]}.{v[5:7]}.{v[0:4]}"

    @app.context_processor
    def _inject():
        me = current_user()
        # Hinweis auf neue Versionen nur fuer Admins (nur sie koennen aktualisieren);
        # andere Besucher loesen auch keine Anfrage bei GitHub aus
        update = (version.verfuegbar(version.datei(app.config["DB_PATH"]), app.config["UPDATE_CHECK"])
                  if me and me["is_admin"] else None)
        return {"me": me, "stats": trails.stats(get_conn()) if me else None,
                "darf_schreiben": users.darf_schreiben(me), "rollen": users.ROLLEN, "rolle": users.rolle,
                "typen": trails.TYP_LABEL, "grade": trails.GRAD_LABEL,
                "regionen": trails.REGION_LABEL, "app_version": version.stand(config.REPO_ROOT),
                "repo_url": version.REPO_URL, "update": update}

    @app.after_request
    def _kein_seitencache(resp):
        # Seiten zeigen den aktuellen Stand der Liste: nicht zwischenspeichern, auch nicht fuer
        # "Zurueck" im Browser (sonst steht ein eben gemachter Trail scheinbar noch im Archiv).
        # Bilder, CSS usw. behalten ihre eigenen Cache-Zeiten.
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(403)
    def _forbidden(_):
        return render_template("fehler.html", fehler="Dafür fehlt dir die Berechtigung."), 403

    @app.errorhandler(404)
    def _notfound(_):
        return render_template("fehler.html", fehler="Seite nicht gefunden."), 404

    @app.errorhandler(413)
    def _zugross(_):
        return render_template("fehler.html", fehler="Die Datei ist zu gross (höchstens 15 MB)."), 413

    # ---- Login / Logout ------------------------------------------------ #
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if users.count(get_conn()) == 0:
            return render_template("login.html", kein_benutzer=True)
        if request.method == "POST":
            name = (request.form.get("username") or "").strip().lower()
            if _locked(name):
                flash(f"Zu viele Fehlversuche – bitte in {config.LOGIN_LOCK_SECONDS // 60} Minuten erneut.", "fehler")
                return render_template("login.html")
            u = users.authenticate(get_conn(), name, request.form.get("password", ""))
            if u:
                nxt = request.args.get("next") or ""
                session.clear()
                session["user"] = u["username"]
                session.permanent = False
                _ATTEMPTS.pop(name, None)
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
            _fail(name)
            flash("Benutzername oder Passwort falsch.", "fehler")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---- Trail-Liste --------------------------------------------------- #
    @app.route("/")
    @login_required
    def index():
        f = request.args.get("f", "alle")
        if f not in ("alle", "offen", "gemacht", "neu"):
            f = "alle"
        q = (request.args.get("q") or "").strip()[:80]
        # Mehrfach-Filter aus den Spaltenkoepfen (?typ=mini&typ=go ...)
        sel = {"typ": [t for t in request.args.getlist("typ") if t in trails.TYPEN],
               "region": [r[:60] for r in request.args.getlist("region") if r][:30],
               "dauer": [d[:50] for d in request.args.getlist("dauer") if d][:30],
               "grad": [g for g in request.args.getlist("grad") if g in trails.GRADE or g == trails.LEER],
               "erfasst": [e[:60] for e in request.args.getlist("erfasst") if e][:30]}
        sort = request.args.get("sort", "ort")
        if sort not in trails.SORTS:
            sort = "ort"
        richtung = "desc" if request.args.get("dir") == "desc" else "asc"
        if f == "neu" and "sort" not in request.args:
            sort, richtung = "neu", "desc"           # neueste zuerst
        # Ansicht Liste/Kacheln: aus der URL, sonst die zuletzt gewaehlte (Sitzung)
        ansicht = request.args.get("ansicht") or session.get("ansicht") or "kacheln"
        if ansicht not in ("liste", "kacheln"):
            ansicht = "kacheln"
        session["ansicht"] = ansicht
        conn = get_conn()
        rows = trails.list_active(conn, f, q, sort=sort, richtung=richtung, **sel)

        def index_url(**over):
            """URL der Liste mit dem aktuellen Zustand, einzelne Werte ueberschreibbar.
            Defaults (alle, ort aufsteigend) und leere Werte tauchen nicht in der URL auf."""
            params = {"f": f if f != "alle" else None, "q": q or None,
                      "sort": sort if sort != "ort" else None,
                      "dir": richtung if richtung != "asc" else None, **sel}
            params.update(over)
            return url_for("index", **{k: v for k, v in params.items() if v not in (None, "", [])})

        sp = trails.spalten_aus(current_user().get("spalten"))
        return render_template("index.html", rows=rows, f=f, q=q, sel=sel, sort=sort, ansicht=ansicht,
                               sp=sp, spalten=trails.SPALTEN,
                               sp_standard=(tuple(sp) == trails.SPALTEN_STANDARD),
                               richtung=richtung, opts=trails.filter_options(conn), index_url=index_url)

    @app.route("/spalten", methods=["POST"])
    @login_required
    def spalten_speichern():
        """Sichtbare Spalten der Liste fuer den angemeldeten Benutzer speichern."""
        if request.form.get("aktion") == "standard":
            users.set_spalten(get_conn(), current_user()["username"], None)
            flash("Spalten auf Standard zurückgesetzt.")
        else:
            gewaehlt = [k for k in trails.SPALTEN_KEYS if k in request.form.getlist("spalte")]
            users.set_spalten(get_conn(), current_user()["username"], gewaehlt)
            flash("Spaltenauswahl gespeichert.")
        nxt = request.form.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))

    @app.route("/statistik")
    @login_required
    def statistik():
        return render_template("statistik.html", st=trails.statistik(get_conn()))

    @app.route("/archiv")
    @login_required
    def archiv():
        q = (request.args.get("q") or "").strip()[:80]
        sort = request.args.get("sort", "ort")
        if sort not in trails.ARCHIV_SORTS:
            sort = "ort"
        richtung = "desc" if request.args.get("dir") == "desc" else "asc"

        def archiv_url(**over):
            params = {"q": q or None, "sort": sort if sort != "ort" else None,
                      "dir": richtung if richtung != "asc" else None}
            params.update(over)
            return url_for("archiv", **{k: v for k, v in params.items() if v not in (None, "")})

        # index_url: Name, den das Makro sortlink erwartet
        return render_template("archiv.html", rows=trails.list_archiv(get_conn(), q, sort, richtung),
                               q=q, sort=sort, richtung=richtung, index_url=archiv_url)

    @app.route("/trail/<int:tid>", methods=["GET", "POST"])
    @login_required
    def trail_edit(tid):
        conn = get_conn()
        t = trails.get(conn, tid)
        if not t:
            abort(404)
        if request.method == "POST":
            if not users.darf_schreiben(current_user()):
                abort(403)
            try:
                if t["quelle"] == "manual":
                    trails.update_manual(conn, tid, request.form)
                neu = trails.update_done(conn, tid, request.form, current_user()["username"],
                                         datum_pflicht=True)
                if neu["gemacht"] and not t["gemacht"] and not request.form.get("gemacht"):
                    flash("Gespeichert – als gemacht markiert (Datum bzw. Mitspieler eingetragen).")
                else:
                    flash("Gespeichert.")
                return redirect(request.form.get("next") or url_for("index"))
            except trails.TrailError as ex:
                conn.rollback()                     # nichts halb speichern (manuelle Felder)
                flash(str(ex), "fehler")
                t = dict(t, **{k: request.form.get(k, "") for k in
                               ("gemacht_datum", "mitspieler", "bemerkung")})
                t["gemacht"] = 1 if request.form.get("gemacht") else 0
        return render_template("trail_form.html", t=t, next=request.args.get("next") or
                               request.form.get("next") or url_for("index"))

    @app.route("/trail/neu", methods=["GET", "POST"])
    @schreiben_required
    def trail_new():
        if request.method == "POST":
            try:
                tid = trails.add_manual(get_conn(), request.form, current_user()["username"], datum_pflicht=True)
                flash("Trail manuell erfasst.")
                return redirect(url_for("trail_edit", tid=tid))
            except trails.TrailError as ex:
                flash(str(ex), "fehler")
        return render_template("trail_new.html", form=request.form)

    @app.route("/trail/<int:tid>/loeschen", methods=["POST"])
    @schreiben_required
    def trail_delete(tid):
        try:
            trails.delete_manual(get_conn(), tid)
            flash("Manuell erfasster Trail gelöscht.")
        except trails.TrailError as ex:
            flash(str(ex), "fehler")
        return redirect(url_for("index"))

    @app.route("/trail/<int:tid>/zuruecksetzen", methods=["POST"])
    @schreiben_required
    def trail_zuruecksetzen(tid):
        conn = get_conn()
        try:
            alt = trails.zuruecksetzen(conn, tid)
        except trails.TrailError as ex:
            flash(str(ex), "fehler")
            return redirect(url_for("index"))
        fotos.entfernen(app.config["FOTO_DIR"], alt)
        flash("Einträge zurückgesetzt – der Trail ist wieder offen.")
        return redirect(url_for("trail_edit", tid=tid, next=request.form.get("next") or None))

    # ---- Import der eigenen Bestellungen (foxtrail.ch-Konto) ------------ #
    @app.route("/import", methods=["GET", "POST"])
    @admin_required                     # schreibt viele Trails auf einmal - nur Admins
    def import_bestellungen():
        conn = get_conn()
        if request.method == "POST" and request.form.get("schritt") == "schreiben":
            try:
                eintraege = bestellungen.eintraege_aus_json(request.form.get("daten", ""))
            except ValueError:
                flash("Ungültige Daten – bitte die Datei nochmals prüfen.", "fehler")
                return redirect(url_for("import_bestellungen"))
            plan = bestellungen.zuordnen(conn, eintraege, benutzer=current_user()["username"])
            res = bestellungen.anwenden(conn, plan, current_user()["username"], app.config["FOTO_DIR"])
            msg = (f"Import: {res['gesetzt']} als gemacht eingetragen, {res['ergaenzt']} ergänzt, "
                   f"{res['gekennzeichnet']} als Import gekennzeichnet, {res['fotos']} Schlussfoto(s) geladen")
            if res["foto_fehler"]:
                msg += f", {res['foto_fehler']} Foto(s) nicht ladbar"
            flash(msg + ".")
            return redirect(url_for("index", f="gemacht"))
        zeilen, daten = None, ""
        if request.method == "POST":
            f = request.files.get("datei")
            if request.form.get("link"):
                # Konto-Link: nur fuer diesen Abruf, nie speichern oder anzeigen
                try:
                    inhalt = json.dumps(bestellungen.abrufen(request.form["link"]))
                except bestellungen.AbrufFehler as ex:
                    flash(str(ex), "fehler")
                    inhalt = ""
            else:
                inhalt = f.read().decode("utf-8", "replace") if f and f.filename else (request.form.get("text") or "")
            eintraege = bestellungen.parse(inhalt) if inhalt else []
            if not inhalt:
                pass                                   # Abruf gescheitert, Meldung steht schon da
            elif not eintraege:
                flash("Keine Bestellungen gefunden. Erwartet wird die Datei konto.json oder der Text "
                      "der Seite „Deine Bestellungen“.", "fehler")
            else:
                zeilen = [{"e": e, "t": t, "aktion": aktion, "grund": grund,
                           "spielzeit": trails.spielzeit_label(trails.spielzeit_min(e["start"], e["ziel"]))}
                          for e, t, aktion, grund in bestellungen.zuordnen(
                              conn, eintraege, benutzer=current_user()["username"])]
                daten = json.dumps(eintraege, ensure_ascii=False)
        n = {a: sum(1 for z in (zeilen or []) if z["aktion"] == a)
             for a in ("setzen", "ergaenzen", "uebersprungen", "unbekannt")}
        n_kenn = sum(1 for z in (zeilen or []) if z["e"].get("_kennzeichnen"))
        return render_template("import.html", zeilen=zeilen, daten=daten, n_setzen=n["setzen"],
                               n_erg=n["ergaenzen"], n_skip=n["uebersprungen"], n_unbekannt=n["unbekannt"],
                               n_kenn=n_kenn)

    # ---- Bilder ------------------------------------------------------- #
    # Die URLs tragen den Dateinamen als ?v=..., ein ersetztes Foto hat also eine neue URL
    # und darf lange im Browser-Cache liegen.
    @app.route("/foto/<int:tid>")
    @login_required
    def foto(tid):
        t = trails.get(get_conn(), tid)
        if not t or not t.get("foto"):
            abort(404)
        if request.args.get("g") == "klein":
            pfad = fotos.vorschau(app.config["FOTO_DIR"], t["foto"])
            if pfad:
                return send_file(pfad, mimetype="image/jpeg", max_age=30 * 86400)
        return send_from_directory(app.config["FOTO_DIR"], t["foto"], max_age=30 * 86400)

    @app.route("/titelbild/<int:tid>")
    @login_required
    def titelbild(tid):
        t = trails.get(get_conn(), tid)
        pfad = fotos.titelbild(app.config["FOTO_DIR"], tid, t.get("bild_url")) if t else None
        if not pfad:
            abort(404)
        return send_file(pfad, mimetype="image/jpeg", max_age=7 * 86400)

    @app.route("/trail/<int:tid>/foto", methods=["POST"])
    @schreiben_required
    def trail_foto(tid):
        """Schlussfoto hochladen/ersetzen, loeschen oder von foxtrail.ch neu laden."""
        conn = get_conn()
        t = trails.get(conn, tid)
        if not t:
            abort(404)
        ordner, aktion = app.config["FOTO_DIR"], request.form.get("aktion")
        if aktion == "hochladen":
            f = request.files.get("datei")
            try:
                name = fotos.speichern(ordner, tid, f.read() if f and f.filename else b"")
            except fotos.FotoError as ex:
                flash(str(ex), "fehler")
            else:
                fotos.entfernen(ordner, t.get("foto"))
                trails.set_foto(conn, tid, name)
                if t["gemacht"]:
                    flash("Foto gespeichert.")
                else:
                    # Ein Schlussfoto heisst: dort gewesen - "gemacht" braucht aber ein Datum
                    flash("Foto gespeichert. Bitte noch das Datum eintragen und speichern, "
                          "dann ist der Trail als gemacht markiert.", "hinweis")
                    return redirect(url_for("trail_edit", tid=tid, next=request.form.get("next") or None,
                                            gemacht=1) + "#eintraege")
        elif aktion == "loeschen" and t.get("foto"):
            fotos.entfernen(ordner, t["foto"])
            trails.set_foto(conn, tid, "")          # '' = bewusst geloescht, Import laedt es nicht neu
            flash("Foto gelöscht.")
        elif aktion == "foxtrail" and t.get("foto_url"):
            name = bestellungen.lade_foto(t["foto_url"], ordner, tid)
            if name:
                if t.get("foto") and t["foto"] != name:
                    fotos.entfernen(ordner, t["foto"])
                fotos.entfernen_vorschau(ordner, name)     # gleicher Name, alte Vorschau weg
                trails.set_foto(conn, tid, name)
                flash("Schlussfoto von foxtrail.ch geladen.")
            else:
                flash("Das Schlussfoto ist auf foxtrail.ch nicht mehr abrufbar.", "fehler")
        return redirect(url_for("trail_edit", tid=tid, next=request.form.get("next") or None) + "#foto")

    # ---- Profil -------------------------------------------------------- #
    @app.route("/profil", methods=["GET", "POST"])
    @login_required
    def profil():
        if request.method == "POST":
            try:
                users.change_own_password(get_conn(), current_user()["username"],
                                          request.form.get("old", ""), request.form.get("new", ""),
                                          request.form.get("new2", ""))
                flash("Passwort geändert.")
                return redirect(url_for("index"))
            except users.UserError as ex:
                flash(str(ex), "fehler")
        return render_template("profil.html")

    # ---- Admin: Benutzer ----------------------------------------------- #
    @app.route("/admin/benutzer")
    @admin_required
    def user_admin():
        return render_template("benutzer.html", userlist=users.list_users(get_conn()))

    @app.route("/admin/benutzer/anlegen", methods=["POST"])
    @admin_required
    def user_add():
        try:
            users.add(get_conn(), request.form.get("username", ""), request.form.get("password", ""),
                      rolle=request.form.get("rolle") or "bearbeiten")
            flash("Benutzer angelegt.")
        except users.UserError as ex:
            flash(str(ex), "fehler")
        return redirect(url_for("user_admin"))

    @app.route("/admin/benutzer/<name>", methods=["POST"])
    @admin_required
    def user_update(name):
        conn = get_conn()
        action = request.form.get("action")
        try:
            if action == "passwort":
                users.set_password(conn, name, request.form.get("password", ""))
                flash(f"Passwort für „{name}“ gesetzt.")
            elif action == "rolle":
                users.update(conn, name, rolle=request.form.get("rolle", ""))
                flash("Gespeichert.")
            elif action == "aktiv":
                users.update(conn, name, active=bool(request.form.get("active")))
                flash("Gespeichert.")
            elif action == "loeschen":
                if name == current_user()["username"]:
                    raise users.UserError("Du kannst dich nicht selbst löschen.")
                users.delete(conn, name)
                flash(f"Benutzer „{name}“ gelöscht.")
        except users.UserError as ex:
            conn.rollback()
            flash(str(ex), "fehler")
        return redirect(url_for("user_admin"))

    # ---- Admin: Abgleich ----------------------------------------------- #
    @app.route("/admin/sync", methods=["GET", "POST"])
    @admin_required
    def sync_admin():
        conn = get_conn()
        if request.method == "POST":
            res = sync.run(conn, ausloeser=f"web:{current_user()['username']}")
            if res["ok"]:
                flash(f"Abgleich ok: {res['gefunden']} gefunden, {res['neu']} neu, "
                      f"{res['aktualisiert']} aktualisiert, {res['reaktiviert']} reaktiviert, "
                      f"{res['archiviert']} ins Archiv, {res['nicht_mehr_im_angebot']} gemachte "
                      f"nicht mehr im Angebot.")
            else:
                flash("Abgleich fehlgeschlagen: " + res["meldung"], "fehler")
            return redirect(url_for("sync_admin"))
        letzter_auto = conn.execute("SELECT ts, ok FROM sync_log WHERE ausloeser = 'timer' "
                                    "ORDER BY id DESC LIMIT 1").fetchone()
        return render_template("sync.html", runs=sync.last_runs(conn), list_url=config.LIST_URL,
                               timer=sync.timer_status()
                               or zeitplan.status(zeitplan.datei(app.config["DB_PATH"])),
                               letzter_auto=letzter_auto)

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    return app
