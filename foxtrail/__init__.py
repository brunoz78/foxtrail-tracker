# -*- coding: utf-8 -*-
"""
Foxtrail-Tracker - Flask-App.

Login: Benutzer in SQLite mit gehashtem Passwort, signierte
Session (reines Sitzungscookie), Sperre nach zu vielen Fehlversuchen (In-Memory), optional
Zweitfaktor (Authenticator-App oder Passkey), Anmelde-Protokoll. Rollen: Administrator,
Bearbeiten, Nur lesen. Wer einen Passkey hinterlegt hat, kann sich damit auch ganz ohne Passwort
anmelden - der Browser sucht den Passkey selbst.
"""

import base64
import datetime
import functools
import json
import time

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)

from . import (authlog, bestellungen, config, db, fotos, i18n, sync, trails, twofa, users, version,
               zeitplan)
from .i18n import tr

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

    # ---- Sprache -------------------------------------------------------- #
    app.jinja_env.globals["_"] = tr

    @app.before_request
    def _sprache():
        g.sprache = i18n.sprache_fuer(current_user(), session, request.accept_languages)

    @app.route("/sprache", methods=["POST"])
    def sprache_setzen():
        """Sprache waehlen: angemeldet beim Benutzer gespeichert, sonst in der Sitzung."""
        wahl = request.form.get("sprache", "")
        if wahl in i18n.SPRACHEN:
            session["sprache"] = wahl
            me = current_user()
            if me:
                users.set_sprache(get_conn(), me["username"], wahl)
        nxt = request.form.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))

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

    def _ip():
        xff = request.headers.get("X-Forwarded-For", "")
        return (xff.split(",")[0].strip() if xff else request.remote_addr) or ""

    def _log(ereignis, benutzer, detail=""):
        """Anmelde-Protokoll: benutzer = wer handelt, detail = wen/was es betraf."""
        authlog.schreiben(get_conn(), ereignis, benutzer, _ip(), request.user_agent.string, detail)

    def _locked(name):
        rec = _ATTEMPTS.get(name)
        return bool(rec and rec[1] > time.time())

    def _fail(name, ereignis="fail"):
        rec = _ATTEMPTS.setdefault(name, [0, 0])
        rec[0] += 1
        _log(ereignis, name)
        if rec[0] >= config.LOGIN_MAX_FAILS:
            rec[1] = time.time() + config.LOGIN_LOCK_SECONDS
            rec[0] = 0
            _log("lock", name)

    def _neue_sitzung(**werte):
        """Sitzung neu beginnen (gegen Session-Fixation), die gewaehlte Sprache behalten."""
        sprache = session.get("sprache")
        session.clear()
        if sprache:
            session["sprache"] = sprache
        session.update(werte)
        session.permanent = False

    def _fertig_anmelden(username, detail=""):
        nxt = session.get("login_next") or ""
        _neue_sitzung(user=username)
        _ATTEMPTS.pop(username, None)
        users.angemeldet(get_conn(), username)
        _log("login", username, detail)
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))

    def _offen():
        """Benutzer im 2FA-Zwischenschritt (Passwort stimmt, zweiter Faktor fehlt noch)."""
        u = users.get(get_conn(), session.get("2fa_user"))
        return u if (u and u["active"]) else None

    @app.before_request
    def _passwortwechsel_erzwingen():
        """Wer sein Passwort aendern muss, kommt nur noch auf diese Seite (oder raus)."""
        if request.endpoint in (None, "static", "pw_aendern", "logout", "sprache_setzen", "healthz"):
            return None
        me = current_user()
        if me and me.get("pw_wechsel") and not me.get("pw_fest"):
            return redirect(url_for("pw_aendern"))
        return None

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
                "sprache": g.get("sprache", i18n.STANDARD), "sprachen": i18n.SPRACHEN,
                "darf_schreiben": users.darf_schreiben(me), "rollen": users.ROLLEN, "rolle": users.rolle,
                "anzeigename": users.anzeigename,
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
        me = current_user()
        if me:
            _log("denied", me["username"], request.path)
        return render_template("fehler.html", fehler=tr("Dafür fehlt dir die Berechtigung.")), 403

    @app.errorhandler(404)
    def _notfound(_):
        return render_template("fehler.html", fehler=tr("Seite nicht gefunden.")), 404

    @app.errorhandler(413)
    def _zugross(_):
        return render_template("fehler.html", fehler=tr("Die Datei ist zu gross (höchstens 15 MB).")), 413

    # ---- Login / Logout ------------------------------------------------ #
    def _passkey_login():
        """Anmeldung mit Passkey anbieten? Nur im sicheren Kontext und wenn es einen gibt."""
        return twofa.passkey_possible(request) and bool(users.mit_passkey(get_conn()))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if users.count(get_conn()) == 0:
            return render_template("login.html", kein_benutzer=True, passkey_login=False, auto=False)
        if session.get("2fa_user") and request.method == "GET":
            return redirect(url_for("login_2fa"))
        if request.method == "POST":
            name = (request.form.get("username") or "").strip().lower()
            if _locked(name):
                flash(tr("Zu viele Fehlversuche – bitte in {n} Minuten erneut.", n=config.LOGIN_LOCK_SECONDS // 60),
                      "fehler")
                return render_template("login.html", passkey_login=_passkey_login(), auto=False)
            u = users.authenticate(get_conn(), name, request.form.get("password", ""))
            if u:
                _neue_sitzung(login_next=request.args.get("next") or "")
                if users.zweitfaktor_noetig(u):
                    session["2fa_user"] = u["username"]
                    return redirect(url_for("login_2fa"))
                if u["twofa_pflicht"] and not users.has_2fa(u):
                    session["2fa_user"] = u["username"]
                    session["2fa_einrichten"] = True
                    flash(tr("Für dieses Konto ist ein zweiter Faktor Pflicht – bitte jetzt einrichten."), "hinweis")
                    return redirect(url_for("twofa_setup"))
                return _fertig_anmelden(u["username"])
            _fail(name)
            flash(tr("Benutzername oder Passwort falsch."), "fehler")
        return render_template("login.html", passkey_login=_passkey_login(),
                               auto=request.method == "GET")

    @app.route("/login/2fa", methods=["GET", "POST"])
    def login_2fa():
        u = _offen()
        if u is None:
            return redirect(url_for("login"))
        if not users.has_2fa(u):
            session["2fa_einrichten"] = True
            return redirect(url_for("twofa_setup"))
        if request.method == "POST":
            if _locked(u["username"]):
                flash(tr("Zu viele Fehlversuche – bitte in {n} Minuten erneut.", n=config.LOGIN_LOCK_SECONDS // 60),
                      "fehler")
            elif twofa.verify_totp(u.get("totp_secret"), request.form.get("code", "")):
                return _fertig_anmelden(u["username"])
            else:
                _fail(u["username"], "fail2fa")
                flash(tr("Der Code stimmt nicht."), "fehler")
        return render_template("login_2fa.html", u=u, hat_totp=bool(u.get("totp_secret")),
                               hat_passkey=bool(users.passkeys(u)), passkey_ok=twofa.passkey_possible(request))

    @app.route("/logout")
    def logout():
        if session.get("user"):
            _log("logout", session["user"])
        sprache = g.get("sprache")
        session.clear()
        if sprache:
            session["sprache"] = sprache
        # abgemeldet=1: die Anmeldeseite fragt dieses eine Mal nicht von selbst nach dem Passkey
        return redirect(url_for("login", abgemeldet=1))

    # ---- Anmelden mit Passkey statt Passwort --------------------------- #
    @app.route("/login/passkey/options")
    def wa_login_options():
        """Anfrage ohne Liste: der Browser sucht selbst einen Passkey fuer diese Seite."""
        if not twofa.passkey_possible(request):
            return jsonify({"error": tr("Die Anmeldung mit Passkey geht nur über HTTPS mit einem "
                                        "Hostnamen.")}), 400
        opts_json, challenge = twofa.auth_options([], request, uv=True)
        session["pk_challenge"] = base64.urlsafe_b64encode(challenge).decode()
        session["login_next"] = request.args.get("next") or ""
        return Response(opts_json, mimetype="application/json")

    @app.route("/login/passkey/verify", methods=["POST"])
    def wa_login_verify():
        if "pk_challenge" not in session:
            return jsonify({"error": tr("Sitzung abgelaufen – bitte neu laden.")}), 400
        challenge = base64.urlsafe_b64decode(session.pop("pk_challenge"))
        body = request.get_data(as_text=True)
        cid = twofa.credential_id_of(body)
        u, stored = users.by_passkey(get_conn(), cid, twofa.user_handle_of(body))
        if u is None:
            _log("fail", "", "Passkey ohne Konto")
            return jsonify({"error": tr("Dieser Passkey gehört zu keinem Konto.")}), 400
        if _locked(u["username"]):
            return jsonify({"error": tr("Zu viele Fehlversuche – bitte in {n} Minuten erneut.",
                                        n=config.LOGIN_LOCK_SECONDS // 60)}), 429
        try:
            neu = twofa.auth_verify(request, challenge, body, stored, uv=True)
        except Exception as ex:                              # noqa: BLE001 - Meldung an den Browser
            _fail(u["username"], "fail2fa")
            return jsonify({"error": str(ex)[:200]}), 400
        users.update_passkey_counter(get_conn(), u["username"], cid, neu)
        return jsonify({"ok": True, "weiter": _fertig_anmelden(u["username"], "Passkey").location})

    # ---- Zweitfaktor: Einrichten und Passkeys -------------------------- #
    def _twofa_person():
        """Wer richtet gerade 2FA ein: Pflicht-Einrichtung beim Login oder angemeldet (Profil)."""
        if session.get("2fa_einrichten") and _offen() is not None:
            return _offen(), True
        me = current_user()
        return (me, False) if me else (None, False)

    @app.route("/2fa/einrichten", methods=["GET", "POST"])
    def twofa_setup():
        u, pflicht = _twofa_person()
        if u is None:
            return redirect(url_for("login"))
        if not session.get("totp_neu"):
            session["totp_neu"] = twofa.new_secret()
        secret = session["totp_neu"]
        if request.method == "POST":
            if twofa.verify_totp(secret, request.form.get("code", "")):
                users.set_totp(get_conn(), u["username"], secret)
                session.pop("totp_neu", None)
                _log("twofa_on", u["username"], "TOTP")
                flash(tr("Authenticator-App eingerichtet."))
                if pflicht:
                    return _fertig_anmelden(u["username"])
                return redirect(url_for("profil"))
            flash(tr("Der Code stimmt nicht – bitte nochmals versuchen."), "fehler")
        uri = twofa.totp_uri(u["username"], secret)
        return render_template("twofa_setup.html", u=u, pflicht=pflicht, secret=secret, qr=twofa.qr_svg(uri),
                               passkey_ok=twofa.passkey_possible(request))

    @app.route("/2fa/passkey/register-options")
    def wa_register_options():
        u, _ = _twofa_person()
        if u is None:
            return jsonify({"error": tr("Nicht angemeldet.")}), 403
        opts_json, challenge = twofa.reg_options(u, users.passkeys(u), request)
        session["wa_challenge"] = base64.urlsafe_b64encode(challenge).decode()
        return Response(opts_json, mimetype="application/json")

    @app.route("/2fa/passkey/register-verify", methods=["POST"])
    def wa_register_verify():
        u, pflicht = _twofa_person()
        if u is None or "wa_challenge" not in session:
            return jsonify({"error": tr("Sitzung abgelaufen – bitte neu laden.")}), 400
        challenge = base64.urlsafe_b64decode(session.pop("wa_challenge"))
        try:
            cred = twofa.reg_verify(request, challenge, request.get_data(as_text=True))
        except Exception as ex:                              # noqa: BLE001 - Meldung an den Browser
            return jsonify({"error": str(ex)[:200]}), 400
        cred["name"] = (request.args.get("name") or "Passkey").strip()[:40]
        cred["created"] = datetime.date.today().isoformat()
        users.add_passkey(get_conn(), u["username"], cred)
        _log("twofa_on", u["username"], f"Passkey „{cred['name']}“")
        weiter = _fertig_anmelden(u["username"]).location if pflicht else url_for("profil")
        return jsonify({"ok": True, "weiter": weiter})

    @app.route("/2fa/passkey/auth-options")
    def wa_auth_options():
        u = _offen()
        if u is None:
            return jsonify({"error": tr("Keine Anmeldung offen.")}), 403
        opts_json, challenge = twofa.auth_options(users.passkeys(u), request)
        session["wa_challenge"] = base64.urlsafe_b64encode(challenge).decode()
        return Response(opts_json, mimetype="application/json")

    @app.route("/2fa/passkey/auth-verify", methods=["POST"])
    def wa_auth_verify():
        u = _offen()
        if u is None or "wa_challenge" not in session:
            return jsonify({"error": tr("Sitzung abgelaufen – bitte neu laden.")}), 400
        if _locked(u["username"]):
            return jsonify({"error": tr("Zu viele Fehlversuche – bitte in {n} Minuten erneut.",
                                        n=config.LOGIN_LOCK_SECONDS // 60)}), 429
        body = request.get_data(as_text=True)
        cid = twofa.credential_id_of(body)
        stored = next((p for p in users.passkeys(u) if p["id"] == cid), None)
        challenge = base64.urlsafe_b64decode(session.pop("wa_challenge"))
        try:
            if stored is None:
                raise ValueError(tr("Unbekannter Passkey."))
            neu = twofa.auth_verify(request, challenge, body, stored)
        except Exception as ex:                              # noqa: BLE001
            _fail(u["username"], "fail2fa")
            return jsonify({"error": str(ex)[:200]}), 400
        users.update_passkey_counter(get_conn(), u["username"], cid, neu)
        return jsonify({"ok": True, "weiter": _fertig_anmelden(u["username"]).location})

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
        spalten = trails.SPALTEN
        if not users.darf_schreiben(current_user()):
            sp = [k for k in sp if k not in trails.SPALTEN_INTERN]
            spalten = [s for s in spalten if s[0] not in trails.SPALTEN_INTERN]
        return render_template("index.html", rows=rows, f=f, q=q, sel=sel, sort=sort, ansicht=ansicht,
                               sp=sp, spalten=spalten,
                               sp_standard=(tuple(sp) == trails.SPALTEN_STANDARD),
                               richtung=richtung, opts=trails.filter_options(conn), index_url=index_url)

    @app.route("/spalten", methods=["POST"])
    @login_required
    def spalten_speichern():
        """Sichtbare Spalten der Liste fuer den angemeldeten Benutzer speichern."""
        if request.form.get("aktion") == "standard":
            users.set_spalten(get_conn(), current_user()["username"], None)
            flash(tr("Spalten auf Standard zurückgesetzt."))
        else:
            gewaehlt = [k for k in trails.SPALTEN_KEYS if k in request.form.getlist("spalte")]
            users.set_spalten(get_conn(), current_user()["username"], gewaehlt)
            flash(tr("Spaltenauswahl gespeichert."))
        nxt = request.form.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))

    @app.route("/statistik")
    @login_required
    def statistik():
        return render_template("statistik.html", st=trails.statistik(get_conn()))

    @app.route("/archiv")
    @schreiben_required                 # "Nur lesen" braucht das Archiv nicht (Wunsch von Bruno)
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
                    flash(tr("Gespeichert – als gemacht markiert (Datum bzw. Mitspieler eingetragen)."))
                else:
                    flash(tr("Gespeichert."))
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
                flash(tr("Trail manuell erfasst."))
                return redirect(url_for("trail_edit", tid=tid))
            except trails.TrailError as ex:
                flash(str(ex), "fehler")
        return render_template("trail_new.html", form=request.form)

    @app.route("/trail/<int:tid>/loeschen", methods=["POST"])
    @schreiben_required
    def trail_delete(tid):
        try:
            trails.delete_manual(get_conn(), tid)
            flash(tr("Manuell erfasster Trail gelöscht."))
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
        flash(tr("Einträge zurückgesetzt – der Trail ist wieder offen."))
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
                flash(tr("Ungültige Daten – bitte die Datei nochmals prüfen."), "fehler")
                return redirect(url_for("import_bestellungen"))
            plan = bestellungen.zuordnen(conn, eintraege, benutzer=current_user()["username"])
            res = bestellungen.anwenden(conn, plan, current_user()["username"], app.config["FOTO_DIR"])
            msg = tr("Import: {gesetzt} als gemacht eingetragen, {ergaenzt} ergänzt, {kenn} als Import "
                     "gekennzeichnet, {fotos} Schlussfoto(s) geladen", gesetzt=res["gesetzt"],
                     ergaenzt=res["ergaenzt"], kenn=res["gekennzeichnet"], fotos=res["fotos"])
            if res["foto_fehler"]:
                msg += ", " + tr("{n} Foto(s) nicht ladbar", n=res["foto_fehler"])
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
                flash(tr("Keine Bestellungen gefunden. Erwartet wird die Datei konto.json oder der Text "
                         "der Seite „Deine Bestellungen“."), "fehler")
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
                    flash(tr("Foto gespeichert."))
                else:
                    # Ein Schlussfoto heisst: dort gewesen - "gemacht" braucht aber ein Datum
                    flash(tr("Foto gespeichert. Bitte noch das Datum eintragen und speichern, "
                             "dann ist der Trail als gemacht markiert."), "hinweis")
                    return redirect(url_for("trail_edit", tid=tid, next=request.form.get("next") or None,
                                            gemacht=1) + "#eintraege")
        elif aktion == "loeschen" and t.get("foto"):
            fotos.entfernen(ordner, t["foto"])
            trails.set_foto(conn, tid, "")          # '' = bewusst geloescht, Import laedt es nicht neu
            flash(tr("Foto gelöscht."))
        elif aktion == "foxtrail" and t.get("foto_url"):
            name = bestellungen.lade_foto(t["foto_url"], ordner, tid)
            if name:
                if t.get("foto") and t["foto"] != name:
                    fotos.entfernen(ordner, t["foto"])
                fotos.entfernen_vorschau(ordner, name)     # gleicher Name, alte Vorschau weg
                trails.set_foto(conn, tid, name)
                flash(tr("Schlussfoto von foxtrail.ch geladen."))
            else:
                flash(tr("Das Schlussfoto ist auf foxtrail.ch nicht mehr abrufbar."), "fehler")
        return redirect(url_for("trail_edit", tid=tid, next=request.form.get("next") or None) + "#foto")

    # ---- Profil -------------------------------------------------------- #
    @app.route("/profil")
    @login_required
    def profil():
        me = current_user()
        return render_template("profil.html", u=me, passkeys=users.passkeys(me),
                               passkey_ok=twofa.passkey_possible(request))

    @app.route("/passwort", methods=["GET", "POST"])
    @login_required
    def pw_aendern():
        me = current_user()
        if me.get("pw_fest"):
            flash(tr("Für dieses Konto kann das Passwort nicht geändert werden."), "fehler")
            return redirect(url_for("profil"))
        erzwungen = bool(me.get("pw_wechsel"))
        if request.method == "POST":
            try:
                users.change_own_password(get_conn(), me["username"], request.form.get("old", ""),
                                          request.form.get("new", ""), request.form.get("new2", ""))
                _log("pw_self", me["username"], "erzwungen" if erzwungen else "freiwillig")
                flash(tr("Passwort geändert."))
                return redirect(url_for("index") if erzwungen else url_for("profil"))
            except users.UserError as ex:
                flash(str(ex), "fehler")
        return render_template("passwort.html", erzwungen=erzwungen)

    @app.route("/profil/totp-entfernen", methods=["POST"])
    @login_required
    def profil_totp_entfernen():
        users.clear_totp(get_conn(), current_user()["username"])
        _log("twofa_off", current_user()["username"], "TOTP")
        flash(tr("Authenticator-App entfernt."))
        return redirect(url_for("profil"))

    @app.route("/profil/passkey-entfernen", methods=["POST"])
    @login_required
    def profil_passkey_entfernen():
        users.remove_passkey(get_conn(), current_user()["username"], request.form.get("id", ""))
        _log("twofa_off", current_user()["username"], "Passkey")
        flash(tr("Passkey entfernt."))
        return redirect(url_for("profil"))

    # ---- Admin: Benutzer ----------------------------------------------- #
    @app.route("/admin/benutzer")
    @admin_required
    def user_admin():
        conn = get_conn()
        edit = users.get(conn, request.args.get("edit")) if request.args.get("edit") else None
        return render_template("benutzer.html", userlist=users.list_users(conn), edit=edit,
                               has_2fa=users.has_2fa, passkeys=users.passkeys)

    @app.route("/admin/benutzer/speichern", methods=["POST"])
    @admin_required
    def user_save():
        conn = get_conn()
        f = request.form
        name = (f.get("username") or "").strip().lower()
        ich = current_user()["username"]
        pw_modus = f.get("pw_modus") or "frei"        # frei | wechsel (Pflicht) | fest (gesperrt)
        werte = dict(rolle=f.get("rolle") or "bearbeiten", anzeigename=f.get("anzeigename", ""),
                     aktiv=bool(f.get("aktiv")), pw_wechsel=pw_modus == "wechsel", pw_fest=pw_modus == "fest",
                     twofa_pflicht=bool(f.get("twofa_pflicht")), sprache=f.get("sprache") or None)
        try:
            if f.get("modus") == "bearbeiten":
                alt = users.get(conn, name)
                if name == ich and not werte["aktiv"]:
                    raise users.UserError(tr("Du kannst dich nicht selbst deaktivieren."))
                neu = users.aendern(conn, name, passwort=f.get("password") or None, **werte)
                _log("user_edit", ich, f"{name}: {users.aenderung_text(alt or {}, neu)}")
                if f.get("password"):
                    _log("pw_admin", ich, f"für Benutzer {name}")
                flash(tr("Benutzer „{name}“ gespeichert.", name=name))
            else:
                neu = users.add(conn, name, f.get("password", ""), **werte)
                _log("user_add", ich, f"{name}: {users.ROLLEN[users.rolle(neu)]}")
                flash(tr("Benutzer „{name}“ angelegt.", name=name))
        except users.UserError as ex:
            conn.rollback()
            flash(str(ex), "fehler")
            return redirect(url_for("user_admin", edit=name if f.get("modus") == "bearbeiten" else None))
        return redirect(url_for("user_admin"))

    @app.route("/admin/benutzer/<name>/loeschen", methods=["POST"])
    @admin_required
    def user_delete(name):
        conn = get_conn()
        try:
            if name == current_user()["username"]:
                raise users.UserError(tr("Du kannst dich nicht selbst löschen."))
            users.delete(conn, name)
            _log("user_del", current_user()["username"], name)
            flash(tr("Benutzer „{name}“ gelöscht.", name=name))
        except users.UserError as ex:
            conn.rollback()
            flash(str(ex), "fehler")
        return redirect(url_for("user_admin"))

    @app.route("/admin/benutzer/<name>/2fa-zuruecksetzen", methods=["POST"])
    @admin_required
    def user_2fa_reset(name):
        try:
            users.clear_2fa(get_conn(), name)
            _log("twofa_reset", current_user()["username"], f"für Benutzer {name}")
            flash(tr("Zweitfaktor von „{name}“ zurückgesetzt.", name=name))
        except users.UserError as ex:
            flash(str(ex), "fehler")
        return redirect(url_for("user_admin"))

    @app.route("/admin/protokoll")
    @admin_required
    def auth_protokoll():
        conn = get_conn()
        wer = (request.args.get("benutzer") or "").strip() or None
        was = request.args.get("ereignis") or None
        if was not in authlog.EREIGNISSE:
            was = None
        try:
            limit = max(50, min(int(request.args.get("n", 500)), 5000))
        except ValueError:
            limit = 500
        namen = sorted({u["username"] for u in users.list_users(conn)} | set(authlog.benutzer_im_protokoll(conn)))
        return render_template("protokoll.html", rows=authlog.lesen(conn, limit, wer, was),
                               ereignisse=authlog.EREIGNISSE, gruppen=authlog.GRUPPEN, f_wer=wer or "",
                               f_was=was or "", limit=limit, namen=namen)

    # ---- Admin: Abgleich ----------------------------------------------- #
    @app.route("/admin/sync", methods=["GET", "POST"])
    @admin_required
    def sync_admin():
        conn = get_conn()
        if request.method == "POST" and request.form.get("aktion") == "zyklus":
            try:
                zeitplan.set_zyklus(conn, request.form.get("zyklus", ""))
                flash(tr("Automatischer Abgleich: {wert}.", wert=tr(zeitplan.ZYKLEN[request.form["zyklus"]])))
            except ValueError:
                flash(tr("Unbekannte Einstellung."), "fehler")
            return redirect(url_for("sync_admin"))
        if request.method == "POST":
            res = sync.run(conn, ausloeser=f"web:{current_user()['username']}")
            if res["ok"]:
                flash(tr("Abgleich ok: {gefunden} gefunden, {neu} neu, {akt} aktualisiert, {reakt} reaktiviert, "
                         "{archiv} ins Archiv, {weg} gemachte nicht mehr im Angebot.", gefunden=res["gefunden"],
                         neu=res["neu"], akt=res["aktualisiert"], reakt=res["reaktiviert"],
                         archiv=res["archiviert"], weg=res["nicht_mehr_im_angebot"]))
            else:
                flash(tr("Abgleich fehlgeschlagen:") + " " + res["meldung"], "fehler")
            return redirect(url_for("sync_admin"))
        letzter_auto = conn.execute("SELECT ts, ok FROM sync_log WHERE ausloeser = 'timer' "
                                    "ORDER BY id DESC LIMIT 1").fetchone()
        return render_template("sync.html", runs=sync.last_runs(conn), list_url=config.LIST_URL,
                               timer=zeitplan.mit_zyklus(conn, sync.timer_status()
                                                         or zeitplan.status(zeitplan.datei(app.config["DB_PATH"]))),
                               letzter_auto=letzter_auto, zyklus=zeitplan.zyklus(conn), zyklen=zeitplan.ZYKLEN)

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    return app
