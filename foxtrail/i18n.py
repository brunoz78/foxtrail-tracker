# -*- coding: utf-8 -*-
"""
Mehrsprachigkeit ohne zusaetzliches Paket.

Ausgangstext ist Deutsch: Templates schreiben {{ _('Speichern') }}, Python-Code tr("Speichern").
Die Uebersetzungen liegen je Sprache in foxtrail/i18n/<code>.json als {"deutscher Text": "..."}.
Fehlt ein Eintrag, erscheint der deutsche Text (tests/test_i18n.py prueft, dass nichts fehlt).
Platzhalter im str.format-Stil: tr("Benutzer „{name}“ angelegt.", name=n).

Welche Sprache gilt (sprache_fuer): Einstellung des Benutzers (users.sprache), sonst die in der
Sitzung gewaehlte (Anmeldeseite), sonst die Browsersprache (Accept-Language), sonst Deutsch.
Ausserhalb einer Anfrage (CLI, Zeitplan) immer Deutsch.
"""

import json
import os

from flask import g, has_request_context

SPRACHEN = {"de": "Deutsch", "fr": "Français", "it": "Italiano", "en": "English"}
STANDARD = "de"
_ORDNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n")
_KATALOGE = {}

WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def katalog(sprache):
    if sprache not in _KATALOGE:
        pfad = os.path.join(_ORDNER, f"{sprache}.json")
        try:
            with open(pfad, encoding="utf-8") as fh:
                _KATALOGE[sprache] = json.load(fh)
        except (OSError, ValueError):
            _KATALOGE[sprache] = {}
    return _KATALOGE[sprache]


def aktuelle():
    if has_request_context():
        return getattr(g, "sprache", STANDARD)
    return STANDARD


def tr(text, **kw):
    """Text in der aktuellen Sprache; kw fuellt {platzhalter}."""
    sprache = aktuelle()
    if sprache != STANDARD and text:
        text = katalog(sprache).get(text) or text
    return text.format(**kw) if kw else text


def sprache_fuer(user, session, accept_languages):
    if user and user.get("sprache") in SPRACHEN:
        return user["sprache"]
    if session.get("sprache") in SPRACHEN:
        return session["sprache"]
    return accept_languages.best_match(list(SPRACHEN), default=STANDARD) or STANDARD
