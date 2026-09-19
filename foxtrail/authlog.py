# -*- coding: utf-8 -*-
"""
Protokoll sicherheitsrelevanter Ereignisse, Tabelle authlog.

"benutzer" ist immer der Handelnde; wen bzw. was es betraf, steht in "detail". Einzelne
Seitenaufrufe werden bewusst nicht protokolliert. Ansicht: Seite /admin/protokoll (nur Admins).
Aufbewahrung: die neuesten MAX_ZEILEN Eintraege, aeltere fallen beim Schreiben weg.
"""

from .db import now

MAX_ZEILEN = 20000

# Schluessel -> deutscher Anzeigetext (uebersetzt wird in der Oberflaeche)
EREIGNISSE = {
    "login": "Anmeldung",
    "logout": "Abmeldung",
    "fail": "Fehlversuch (Passwort)",
    "fail2fa": "Fehlversuch (2FA)",
    "lock": "Gesperrt (5 Min.)",
    "denied": "Zugriff verweigert",
    "user_add": "Benutzer angelegt",
    "user_edit": "Benutzer geändert",
    "user_del": "Benutzer gelöscht",
    "pw_self": "Passwort selbst geändert",
    "pw_admin": "Passwort durch Admin gesetzt",
    "twofa_on": "2FA eingerichtet",
    "twofa_off": "2FA entfernt",
    "twofa_reset": "2FA zurückgesetzt",
}
GRUPPEN = [
    ("Anmeldung", ["login", "logout", "fail", "fail2fa", "lock", "denied"]),
    ("Konten und Rechte", ["user_add", "user_edit", "user_del", "pw_self", "pw_admin",
                           "twofa_on", "twofa_off", "twofa_reset"]),
]


def schreiben(conn, ereignis, benutzer, ip="", ua="", detail=""):
    """Ereignis festhalten. Darf nie eine Anmeldung verhindern -> Fehler schlucken."""
    try:
        conn.execute("INSERT INTO authlog (ts, benutzer, ereignis, detail, ip, ua) VALUES (?,?,?,?,?,?)",
                     (now(), (benutzer or "")[:40], ereignis, (detail or "")[:300], (ip or "")[:64],
                      (ua or "")[:160]))
        conn.execute("DELETE FROM authlog WHERE id <= (SELECT MAX(id) FROM authlog) - ?", (MAX_ZEILEN,))
        conn.commit()
    except Exception:                                   # noqa: BLE001 - Protokoll ist Nebensache
        pass


def lesen(conn, limit=500, benutzer=None, ereignis=None):
    """Neueste zuerst, optional gefiltert."""
    sql, args = "SELECT * FROM authlog WHERE 1=1", []
    if benutzer:
        sql += " AND benutzer = ?"
        args.append(benutzer)
    if ereignis:
        sql += " AND ereignis = ?"
        args.append(ereignis)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def benutzer_im_protokoll(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT benutzer FROM authlog WHERE benutzer != '' "
                                       "ORDER BY benutzer")]
