# -*- coding: utf-8 -*-
"""
Benutzerkonten: anlegen, pruefen, Passwort setzen. Passwoerter werden mit
werkzeug.security (scrypt / pbkdf2) gehasht - nie im Klartext gespeichert.
"""

import re

from werkzeug.security import check_password_hash, generate_password_hash

from .db import now

_NAME_RE = re.compile(r"^[a-z0-9._-]{2,32}$")
MIN_PW_LEN = 8


class UserError(Exception):
    pass


def _check_name(name):
    if not _NAME_RE.match(name or ""):
        raise UserError("Benutzername: 2-32 Zeichen, nur a-z, 0-9, Punkt, Minus, Unterstrich.")


def _check_pw(pw):
    if len(pw or "") < MIN_PW_LEN:
        raise UserError(f"Passwort muss mindestens {MIN_PW_LEN} Zeichen haben.")


def get(conn, username):
    if not username:
        return None
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username.lower(),)).fetchone()
    return dict(row) if row else None


def list_users(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY username")]


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def add(conn, username, password, is_admin=False):
    username = (username or "").strip().lower()
    _check_name(username)
    _check_pw(password)
    if get(conn, username):
        raise UserError(f"Benutzer „{username}“ existiert bereits.")
    conn.execute(
        "INSERT INTO users (username, pw_hash, is_admin, active, created) VALUES (?,?,?,1,?)",
        (username, generate_password_hash(password), 1 if is_admin else 0, now()))
    return get(conn, username)


def authenticate(conn, username, password):
    """Gibt den Benutzer zurueck, wenn Name + Passwort stimmen und das Konto aktiv ist."""
    u = get(conn, (username or "").strip().lower())
    if not u or not u["active"]:
        return None
    if not check_password_hash(u["pw_hash"], password or ""):
        return None
    conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now(), u["id"]))
    return u


def set_password(conn, username, new_password):
    _check_pw(new_password)
    u = get(conn, username)
    if not u:
        raise UserError("Benutzer nicht gefunden.")
    conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?",
                 (generate_password_hash(new_password), u["id"]))


def change_own_password(conn, username, old, new, new2):
    if new != new2:
        raise UserError("Die beiden neuen Passwörter stimmen nicht überein.")
    u = get(conn, username)
    if not u or not check_password_hash(u["pw_hash"], old or ""):
        raise UserError("Altes Passwort ist falsch.")
    set_password(conn, username, new)


def update(conn, username, is_admin=None, active=None):
    u = get(conn, username)
    if not u:
        raise UserError("Benutzer nicht gefunden.")
    if is_admin is not None:
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, u["id"]))
    if active is not None:
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, u["id"]))
    _ensure_one_admin(conn)


def delete(conn, username):
    u = get(conn, username)
    if not u:
        raise UserError("Benutzer nicht gefunden.")
    conn.execute("DELETE FROM users WHERE id = ?", (u["id"],))
    _ensure_one_admin(conn)


def _ensure_one_admin(conn):
    n = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1").fetchone()[0]
    if n == 0:
        raise UserError("Es muss mindestens ein aktiver Administrator übrig bleiben.")
