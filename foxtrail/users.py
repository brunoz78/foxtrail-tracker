# -*- coding: utf-8 -*-
"""
Benutzerkonten: anlegen, pruefen, Passwort setzen, Rechte und Zweitfaktor. Passwoerter werden mit
werkzeug.security (scrypt / pbkdf2) gehasht - nie im Klartext gespeichert.

Aufbau: Anzeigename, Rolle, aktiv, "Passwort beim naechsten
Login aendern" (pw_wechsel), "Passwort darf nicht geaendert werden" (pw_fest), 2FA-Pflicht,
TOTP-Schluessel und Passkeys (JSON-Liste in users.passkeys).
"""

import json
import re

from werkzeug.security import check_password_hash, generate_password_hash

from .db import now
from .i18n import SPRACHEN, tr

_NAME_RE = re.compile(r"^[a-z0-9._-]{2,32}$")
MIN_PW_LEN = 8
_DUMMY_HASH = generate_password_hash("x")           # gleiche Laufzeit bei unbekanntem Namen
_PW_SONDER = set(r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~""")

# Rollen: admin = alles, bearbeiten = Trails eintragen, lesen = nur ansehen
ROLLEN = {"admin": "Administrator", "bearbeiten": "Bearbeiten", "lesen": "Nur lesen"}


class UserError(Exception):
    pass


def rolle(u):
    if u["is_admin"]:
        return "admin"
    return "lesen" if u.get("nur_lesen") else "bearbeiten"


def darf_schreiben(u):
    return bool(u) and not u.get("nur_lesen")


def anzeigename(u):
    return (u.get("anzeigename") or "").strip() or u["username"]


def _flags(rolle_):
    """(is_admin, nur_lesen) fuer eine Rolle."""
    if rolle_ not in ROLLEN:
        raise UserError(tr("Unbekannte Rolle."))
    return (1 if rolle_ == "admin" else 0), (1 if rolle_ == "lesen" else 0)


def _check_name(name):
    if not _NAME_RE.match(name or ""):
        raise UserError(tr("Benutzername: 2-32 Zeichen, nur a-z, 0-9, Punkt, Minus, Unterstrich."))


def _check_pw(pw):
    if len(pw or "") < MIN_PW_LEN:
        raise UserError(tr("Passwort muss mindestens {n} Zeichen haben.", n=MIN_PW_LEN))


def passwort_fehler(pw):
    """Regeln fuer die Selbstbedienung (Profil, erzwungener Wechsel): None oder Fehlertext.
    Der Admin waehlt beim Setzen frei (nur Mindestlaenge)."""
    pw = pw or ""
    if len(pw) < MIN_PW_LEN:
        return tr("Passwort muss mindestens {n} Zeichen haben.", n=MIN_PW_LEN)
    if not any(c.islower() for c in pw):
        return tr("Das Passwort braucht mindestens einen Kleinbuchstaben.")
    if not any(c.isupper() for c in pw):
        return tr("Das Passwort braucht mindestens einen Grossbuchstaben.")
    if not any(c.isdigit() for c in pw):
        return tr("Das Passwort braucht mindestens eine Zahl.")
    if not any(c in _PW_SONDER for c in pw):
        return tr("Das Passwort braucht mindestens ein Sonderzeichen.")
    return None


# ---- Lesen -------------------------------------------------------------------- #
def get(conn, username):
    if not username:
        return None
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username.lower(),)).fetchone()
    return dict(row) if row else None


def list_users(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY username")]


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def passkeys(u):
    try:
        return json.loads((u or {}).get("passkeys") or "[]")
    except ValueError:
        return []


def mit_passkey(conn):
    """Aktive Benutzer, die mindestens einen Passkey hinterlegt haben."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM users WHERE active = 1 AND COALESCE(passkeys, '[]') NOT IN ('', '[]')")]


def by_passkey(conn, cred_id, user_handle=None):
    """(Benutzer, Passkey) zu einer Credential-ID - fuer die Anmeldung ohne Benutzernamen.
    Das user handle aus der Browser-Antwort ist der Benutzername; fehlt es oder passt es nicht,
    wird in allen Konten gesucht. Ohne Treffer (None, None)."""
    if not cred_id:
        return None, None
    zuerst = get(conn, user_handle) if user_handle else None
    for u in ([zuerst] if zuerst and zuerst["active"] else []) + mit_passkey(conn):
        for p in passkeys(u):
            if p.get("id") == cred_id:
                return u, p
    return None, None


def has_2fa(u):
    return bool(u and (u.get("totp_secret") or passkeys(u)))


def authenticate(conn, username, password):
    """Aktiver Benutzer mit passendem Passwort, sonst None (gleiche Laufzeit, auch wenn es den
    Namen nicht gibt). last_login setzt erst die fertige Anmeldung (angemeldet())."""
    u = get(conn, (username or "").strip().lower())
    ok = check_password_hash(u["pw_hash"] if u else _DUMMY_HASH, password or "")
    return u if (u and ok and u["active"]) else None


def angemeldet(conn, username):
    conn.execute("UPDATE users SET last_login = ? WHERE username = ?", (now(), username))


# ---- Schreiben ---------------------------------------------------------------- #
def set_sprache(conn, username, sprache):
    """Sprache der Oberflaeche; None/'' = automatisch (Browser)."""
    if sprache and sprache not in SPRACHEN:
        raise UserError(tr("Unbekannte Sprache."))
    conn.execute("UPDATE users SET sprache = ? WHERE username = ?", (sprache or None, username))


def set_spalten(conn, username, keys):
    """Spaltenauswahl speichern; keys=None setzt auf den Standard zurueck."""
    conn.execute("UPDATE users SET spalten = ? WHERE username = ?",
                 (",".join(keys) if keys is not None else None, username))


def add(conn, username, password, is_admin=False, rolle=None, anzeigename="", pw_wechsel=False,
        pw_fest=False, twofa_pflicht=False, sprache=None, aktiv=True):
    """rolle: admin | bearbeiten | lesen; ohne Angabe entscheidet is_admin."""
    username = (username or "").strip().lower()
    _check_name(username)
    _check_pw(password)
    admin, lesen = _flags(rolle or ("admin" if is_admin else "bearbeiten"))
    if pw_wechsel and pw_fest:
        raise UserError(tr("„Passwort ändern beim nächsten Login“ und „Passwort darf nicht geändert "
                           "werden“ schliessen sich aus."))
    if sprache and sprache not in SPRACHEN:
        raise UserError(tr("Unbekannte Sprache."))
    if get(conn, username):
        raise UserError(tr("Benutzer „{name}“ existiert bereits.", name=username))
    conn.execute(
        "INSERT INTO users (username, pw_hash, is_admin, nur_lesen, active, created, anzeigename, "
        "pw_wechsel, pw_fest, twofa_pflicht, sprache) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (username, generate_password_hash(password), admin, lesen, 1 if aktiv else 0, now(),
         (anzeigename or "").strip()[:60] or None, int(bool(pw_wechsel)), int(bool(pw_fest)),
         int(bool(twofa_pflicht)), sprache or None))
    _ensure_one_admin(conn)
    return get(conn, username)


def aendern(conn, username, *, rolle=None, anzeigename=None, aktiv=None, pw_wechsel=None, pw_fest=None,
            twofa_pflicht=None, sprache=False, passwort=None):
    """Benutzerverwaltung: nur uebergebene Felder aendern (sprache=False heisst unveraendert)."""
    u = get(conn, username)
    if not u:
        raise UserError(tr("Benutzer nicht gefunden."))
    neu_wechsel = u["pw_wechsel"] if pw_wechsel is None else int(bool(pw_wechsel))
    neu_fest = u["pw_fest"] if pw_fest is None else int(bool(pw_fest))
    if neu_wechsel and neu_fest:
        raise UserError(tr("„Passwort ändern beim nächsten Login“ und „Passwort darf nicht geändert "
                           "werden“ schliessen sich aus."))
    felder = {"pw_wechsel": neu_wechsel, "pw_fest": neu_fest}
    if rolle is not None:
        felder["is_admin"], felder["nur_lesen"] = _flags(rolle)
    if anzeigename is not None:
        felder["anzeigename"] = anzeigename.strip()[:60] or None
    if aktiv is not None:
        felder["active"] = int(bool(aktiv))
    if twofa_pflicht is not None:
        felder["twofa_pflicht"] = int(bool(twofa_pflicht))
    if sprache is not False:
        if sprache and sprache not in SPRACHEN:
            raise UserError(tr("Unbekannte Sprache."))
        felder["sprache"] = sprache or None
    if passwort:
        _check_pw(passwort)
        felder["pw_hash"] = generate_password_hash(passwort)
    conn.execute(f"UPDATE users SET {', '.join(k + ' = ?' for k in felder)} WHERE id = ?",
                 (*felder.values(), u["id"]))
    _ensure_one_admin(conn)
    return get(conn, username)


def update(conn, username, is_admin=None, active=None, rolle=None):
    """Kurzform (CLI, Tests): Rolle bzw. aktiv setzen."""
    if is_admin is not None and rolle is None:
        rolle = "admin" if is_admin else "bearbeiten"
    return aendern(conn, username, rolle=rolle, aktiv=active)


def set_password(conn, username, new_password):
    """Passwort setzen (Admin, CLI) - ohne die Regeln der Selbstbedienung."""
    _check_pw(new_password)
    u = get(conn, username)
    if not u:
        raise UserError(tr("Benutzer nicht gefunden."))
    conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (generate_password_hash(new_password), u["id"]))


def change_own_password(conn, username, old, new, new2):
    """Selbstbedienung: altes Passwort pruefen, Regeln erzwingen; loescht pw_wechsel."""
    u = get(conn, username)
    if not u:
        raise UserError(tr("Benutzer nicht gefunden."))
    if u.get("pw_fest"):
        raise UserError(tr("Für dieses Konto kann das Passwort nicht geändert werden."))
    if not check_password_hash(u["pw_hash"], old or ""):
        raise UserError(tr("Altes Passwort ist falsch."))
    if (new or "") != (new2 or ""):
        raise UserError(tr("Die beiden neuen Passwörter stimmen nicht überein."))
    fehler = passwort_fehler(new)
    if fehler:
        raise UserError(fehler)
    if check_password_hash(u["pw_hash"], new):
        raise UserError(tr("Das neue Passwort muss sich vom alten unterscheiden."))
    conn.execute("UPDATE users SET pw_hash = ?, pw_wechsel = 0 WHERE id = ?",
                 (generate_password_hash(new), u["id"]))


def delete(conn, username):
    u = get(conn, username)
    if not u:
        raise UserError(tr("Benutzer nicht gefunden."))
    conn.execute("DELETE FROM users WHERE id = ?", (u["id"],))
    _ensure_one_admin(conn)


def _ensure_one_admin(conn):
    n = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1").fetchone()[0]
    if n == 0:
        raise UserError(tr("Es muss mindestens ein aktiver Administrator übrig bleiben."))


# ---- Protokolltexte (deutsch, wie das uebrige Log) ------------------------------ #
_FLAGS = (("active", "aktiv"), ("pw_wechsel", "Passwortwechsel erzwungen"),
          ("pw_fest", "Passwort gesperrt"), ("twofa_pflicht", "2FA-Pflicht"))


def aenderung_text(alt, neu):
    teile = []
    if rolle(alt) != rolle(neu):
        teile.append(f"Rolle {ROLLEN[rolle(alt)]} → {ROLLEN[rolle(neu)]}")
    if (alt.get("anzeigename") or "") != (neu.get("anzeigename") or ""):
        teile.append(f"Anzeigename „{neu.get('anzeigename') or ''}“")
    for k, label in _FLAGS:
        if bool(alt.get(k)) != bool(neu.get(k)):
            teile.append(f"{label}: {'ja' if neu.get(k) else 'nein'}")
    if (alt.get("sprache") or "") != (neu.get("sprache") or ""):
        teile.append(f"Sprache {neu.get('sprache') or 'automatisch'}")
    return "; ".join(teile) or "keine Änderung"


# ---- Zweitfaktor -------------------------------------------------------------- #
def _set_passkeys(conn, username, liste):
    conn.execute("UPDATE users SET passkeys = ? WHERE username = ?", (json.dumps(liste), username))


def set_totp(conn, username, secret):
    conn.execute("UPDATE users SET totp_secret = ? WHERE username = ?", (secret, username))


def clear_totp(conn, username):
    set_totp(conn, username, None)


def add_passkey(conn, username, cred):
    liste = [p for p in passkeys(get(conn, username)) if p["id"] != cred["id"]]
    _set_passkeys(conn, username, liste + [cred])


def update_passkey_counter(conn, username, cred_id, sign_count):
    liste = passkeys(get(conn, username))
    for p in liste:
        if p["id"] == cred_id:
            p["sign_count"] = sign_count
    _set_passkeys(conn, username, liste)


def remove_passkey(conn, username, cred_id):
    _set_passkeys(conn, username, [p for p in passkeys(get(conn, username)) if p["id"] != cred_id])


def clear_2fa(conn, username):
    if not get(conn, username):
        raise UserError(tr("Benutzer nicht gefunden."))
    conn.execute("UPDATE users SET totp_secret = NULL, passkeys = '[]' WHERE username = ?", (username,))
