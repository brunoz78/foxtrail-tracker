# -*- coding: utf-8 -*-
"""
Foxtrail-Tracker - Flask-App.

Login nach dem Muster eines Session-Logins: Benutzer in SQLite mit gehashtem
Passwort, serverseitig signierte Session (reines Sitzungscookie), Sperre nach
zu vielen Fehlversuchen (In-Memory). Kein 2FA, keine Rollen ausser dem Flag
"Administrator" (Benutzerverwaltung + Abgleich ausloesen).
"""

import functools
import time

from flask import (Flask, abort, flash, g, redirect, render_template, request, session,
                   url_for)

from . import config, db, sync, trails, users

_ATTEMPTS = {}   # username -> [fails, lock_until_ts]


def create_app(test_config=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key()
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.force_https()
    app.config["DB_PATH"] = config.db_path()
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    if test_config:
        app.config.update(test_config)

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

    @app.template_filter("datum")
    def _datum(v):
        """2025-06-01 -> 01.06.2025"""
        if not v or len(v) < 10:
            return v or ""
        return f"{v[8:10]}.{v[5:7]}.{v[0:4]}"

    @app.context_processor
    def _inject():
        return {"me": current_user(), "stats": trails.stats(get_conn()) if current_user() else None,
                "typen": trails.TYP_LABEL, "grade": trails.GRAD_LABEL}

    @app.errorhandler(403)
    def _forbidden(_):
        return render_template("fehler.html", fehler="Dafuer fehlt dir die Berechtigung."), 403

    @app.errorhandler(404)
    def _notfound(_):
        return render_template("fehler.html", fehler="Seite nicht gefunden."), 404

    # ---- Login / Logout ------------------------------------------------ #
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if users.count(get_conn()) == 0:
            return render_template("login.html", kein_benutzer=True)
        if request.method == "POST":
            name = (request.form.get("username") or "").strip().lower()
            if _locked(name):
                flash(f"Zu viele Fehlversuche - bitte in {config.LOGIN_LOCK_SECONDS // 60} Minuten erneut.")
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
            flash("Benutzername oder Passwort falsch.")
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
               "grad": [g for g in request.args.getlist("grad") if g in trails.GRADE]}
        sort = request.args.get("sort", "ort")
        if sort not in trails.SORTS:
            sort = "ort"
        richtung = "desc" if request.args.get("dir") == "desc" else "asc"
        if f == "neu" and "sort" not in request.args:
            sort, richtung = "neu", "desc"           # neueste zuerst
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

        return render_template("index.html", rows=rows, f=f, q=q, sel=sel, sort=sort,
                               richtung=richtung, opts=trails.filter_options(conn), index_url=index_url)

    @app.route("/archiv")
    @login_required
    def archiv():
        return render_template("archiv.html", rows=trails.list_archiv(get_conn()))

    @app.route("/trail/<int:tid>", methods=["GET", "POST"])
    @login_required
    def trail_edit(tid):
        conn = get_conn()
        t = trails.get(conn, tid)
        if not t:
            abort(404)
        if request.method == "POST":
            try:
                if t["quelle"] == "manual":
                    trails.update_manual(conn, tid, request.form)
                trails.update_done(conn, tid, request.form, current_user()["username"])
                flash("Gespeichert.")
                return redirect(request.form.get("next") or url_for("index"))
            except trails.TrailError as ex:
                flash(str(ex))
                t = dict(t, **{k: request.form.get(k, "") for k in
                               ("gemacht_datum", "mitspieler", "bemerkung")})
                t["gemacht"] = 1 if request.form.get("gemacht") else 0
        return render_template("trail_form.html", t=t, next=request.args.get("next") or
                               request.form.get("next") or url_for("index"))

    @app.route("/trail/neu", methods=["GET", "POST"])
    @login_required
    def trail_new():
        if request.method == "POST":
            try:
                tid = trails.add_manual(get_conn(), request.form, current_user()["username"])
                flash("Trail manuell erfasst.")
                return redirect(url_for("trail_edit", tid=tid))
            except trails.TrailError as ex:
                flash(str(ex))
        return render_template("trail_new.html", form=request.form)

    @app.route("/trail/<int:tid>/loeschen", methods=["POST"])
    @login_required
    def trail_delete(tid):
        try:
            trails.delete_manual(get_conn(), tid)
            flash("Manuell erfasster Trail geloescht.")
        except trails.TrailError as ex:
            flash(str(ex))
        return redirect(url_for("index"))

    # ---- Profil -------------------------------------------------------- #
    @app.route("/profil", methods=["GET", "POST"])
    @login_required
    def profil():
        if request.method == "POST":
            try:
                users.change_own_password(get_conn(), current_user()["username"],
                                          request.form.get("old", ""), request.form.get("new", ""),
                                          request.form.get("new2", ""))
                flash("Passwort geaendert.")
                return redirect(url_for("index"))
            except users.UserError as ex:
                flash(str(ex))
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
                      is_admin=bool(request.form.get("is_admin")))
            flash("Benutzer angelegt.")
        except users.UserError as ex:
            flash(str(ex))
        return redirect(url_for("user_admin"))

    @app.route("/admin/benutzer/<name>", methods=["POST"])
    @admin_required
    def user_update(name):
        conn = get_conn()
        action = request.form.get("action")
        try:
            if action == "passwort":
                users.set_password(conn, name, request.form.get("password", ""))
                flash(f"Passwort fuer „{name}“ gesetzt.")
            elif action == "admin":
                users.update(conn, name, is_admin=bool(request.form.get("is_admin")))
                flash("Gespeichert.")
            elif action == "aktiv":
                users.update(conn, name, active=bool(request.form.get("active")))
                flash("Gespeichert.")
            elif action == "loeschen":
                if name == current_user()["username"]:
                    raise users.UserError("Du kannst dich nicht selbst loeschen.")
                users.delete(conn, name)
                flash(f"Benutzer „{name}“ geloescht.")
        except users.UserError as ex:
            conn.rollback()
            flash(str(ex))
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
                flash("Abgleich fehlgeschlagen: " + res["meldung"])
            return redirect(url_for("sync_admin"))
        return render_template("sync.html", runs=sync.last_runs(conn), list_url=config.LIST_URL)

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    return app
