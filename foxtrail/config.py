# -*- coding: utf-8 -*-
"""
Konfiguration ueber Umgebungsvariablen (z. B. systemd EnvironmentFile).

  FOXTRAIL_DB          Pfad zur SQLite-Datenbank
                       (Default: /var/lib/foxtrail-tracker/foxtrail.db falls das
                       Verzeichnis existiert, sonst ./data/foxtrail.local.db)
  SECRET_KEY           Session-Signatur - in Produktion PFLICHT (sonst wird bei
                       jedem Neustart ein zufaelliger Key erzeugt und alle Logins
                       verfallen)
  FORCE_HTTPS          "1" -> Session-Cookie nur ueber HTTPS (hinter Reverse-Proxy)
  FOXTRAIL_FOTOS       Ordner fuer Schlussfotos aus dem Bestellungs-Import
                       (Default: <Ordner der DB>/fotos)
  FOXTRAIL_LIST_URL    Kategorie-URL, die der Scraper abgrast
  SCRAPER_USER_AGENT   User-Agent des Scrapers
  SCRAPER_DELAY        Pause zwischen zwei Seitenabrufen in Sekunden (Default 1.0)
  LOGIN_MAX_FAILS      Fehlversuche bis zur Sperre (Default 5)
  LOGIN_LOCK_SECONDS   Sperrdauer in Sekunden (Default 300)
"""

import os
import secrets

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
SEED_FILE = os.path.join(REPO_ROOT, "data", "trails_seed.json")


def db_path():
    p = os.environ.get("FOXTRAIL_DB", "").strip()
    if p:
        return p
    if os.path.isdir("/var/lib/foxtrail-tracker"):
        return "/var/lib/foxtrail-tracker/foxtrail.db"
    return os.path.join(REPO_ROOT, "data", "foxtrail.local.db")


def foto_dir():
    p = os.environ.get("FOXTRAIL_FOTOS", "").strip()
    return p or os.path.join(os.path.dirname(os.path.abspath(db_path())), "fotos")


def secret_key():
    return os.environ.get("SECRET_KEY") or secrets.token_hex(32)


def force_https():
    return os.environ.get("FORCE_HTTPS", "") == "1"


LIST_URL = os.environ.get("FOXTRAIL_LIST_URL", "https://foxtrail.ch/kategorie/trails/")
USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "foxtrail-tracker/1.0 (+https://github.com/brunoz78/foxtrail-tracker; private Trail-Liste, 1 Abruf/Woche)")
SCRAPER_DELAY = float(os.environ.get("SCRAPER_DELAY", "1.0"))
LOGIN_MAX_FAILS = int(os.environ.get("LOGIN_MAX_FAILS", "5"))
LOGIN_LOCK_SECONDS = int(os.environ.get("LOGIN_LOCK_SECONDS", "300"))
