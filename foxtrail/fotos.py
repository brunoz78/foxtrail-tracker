# -*- coding: utf-8 -*-
"""
Bilder: Schlussfotos (Import oder von Hand hochgeladen), Vorschaubilder, Titelbilder.

Ablage unter config.foto_dir():
  <id>.jpg | <id>-<zeit>.jpg   Schlussfoto; Dateiname steht in trails.foto
  klein/<dateiname>.jpg        Vorschau (Liste, Kacheln), wird beim ersten Abruf erzeugt
  titel/<id>-<hash>.jpg        Titelbild von foxtrail.ch (trails.bild_url), einmal geladen,
                               damit der Browser nie direkt bei foxtrail.ch anfragt

trails.foto: NULL = kein Foto, '' = von Hand geloescht (der Bestellungs-Import laedt es
dann nicht wieder), sonst Dateiname.

Hochgeladene Fotos werden mit Pillow gedreht (EXIF-Ausrichtung), auf hoechstens
MAX_KANTE Pixel verkleinert und als JPEG neu gespeichert - das entfernt nebenbei die
EXIF-Daten samt GPS-Position. Ohne Pillow (sollte nicht vorkommen, steht in
requirements.txt) wird das Original abgelegt und es gibt keine Vorschaubilder.
"""

import hashlib
import io
import os
import time

import requests

from . import config

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_KANTE = 2560
VORSCHAU_BREITE = 640
TITEL_PREFIX = "https://foxtrail.ch/"

try:
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 60_000_000          # Schutz vor "Dekompressionsbomben"
except ImportError:                               # pragma: no cover
    Image = None


class FotoError(Exception):
    pass


def art(daten):
    """Bildformat an den ersten Bytes erkennen: 'jpg' | 'png' | 'webp' | None."""
    if daten[:3] == b"\xff\xd8\xff":
        return "jpg"
    if daten[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if daten[:4] == b"RIFF" and daten[8:12] == b"WEBP":
        return "webp"
    return None


def _jpeg(daten, kante):
    """Bild drehen, verkleinern, als JPEG-Bytes zurueck. FotoError bei kaputten Dateien."""
    try:
        with Image.open(io.BytesIO(daten)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((kante, kante))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85, optimize=True)
            return out.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError) as ex:
        raise FotoError("Das Bild lässt sich nicht lesen.") from ex


def _schreiben(pfad, daten):
    """Atomar schreiben: parallele Abrufe derselben Vorschau sehen nie eine halbe Datei."""
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    tmp = f"{pfad}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(daten)
    os.replace(tmp, pfad)


def speichern(foto_dir, trail_id, daten):
    """Hochgeladenes Foto pruefen und ablegen, gibt den neuen Dateinamen zurueck.
    Der Zeitstempel im Namen sorgt dafuer, dass der Browser ein ersetztes Foto neu laedt."""
    if not daten:
        raise FotoError("Keine Datei ausgewählt.")
    if len(daten) > MAX_UPLOAD_BYTES:
        raise FotoError(f"Das Bild ist grösser als {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    endung = art(daten)
    if not endung:
        raise FotoError("Nur JPEG-, PNG- oder WebP-Bilder.")
    if Image is not None:
        daten, endung = _jpeg(daten, MAX_KANTE), "jpg"
    name = f"{trail_id}-{int(time.time())}.{endung}"
    _schreiben(os.path.join(foto_dir, name), daten)
    return name


def entfernen(foto_dir, name):
    """Foto und seine Vorschau loeschen; fehlende Dateien sind kein Fehler."""
    if not name:
        return
    for p in (os.path.join(foto_dir, name), _vorschau_pfad(foto_dir, name)):
        try:
            os.remove(p)
        except OSError:
            pass


def entfernen_vorschau(foto_dir, name):
    try:
        os.remove(_vorschau_pfad(foto_dir, name))
    except OSError:
        pass


def _vorschau_pfad(foto_dir, name):
    return os.path.join(foto_dir, "klein", os.path.splitext(name)[0] + ".jpg")


def vorschau(foto_dir, name):
    """Pfad der Vorschau zum Foto `name`, bei Bedarf erzeugt. None, wenn das nicht geht
    (kein Pillow, kaputtes Bild) - dann liefert der Aufrufer das Original aus."""
    ziel = _vorschau_pfad(foto_dir, name)
    if os.path.exists(ziel):
        return ziel
    if Image is None:
        return None
    try:
        with open(os.path.join(foto_dir, name), "rb") as fh:
            daten = _jpeg(fh.read(), VORSCHAU_BREITE)
        _schreiben(ziel, daten)
        return ziel
    except (OSError, FotoError):
        return None


def titelbild(foto_dir, trail_id, url):
    """Pfad des zwischengespeicherten Titelbilds; laedt es beim ersten Mal von foxtrail.ch.
    None, wenn es keins gibt oder der Abruf scheitert. Der Hash der URL im Dateinamen
    sorgt dafuer, dass ein neues Titelbild auf foxtrail.ch auch hier neu geladen wird."""
    if not url or not url.startswith(TITEL_PREFIX):
        return None
    ordner = os.path.join(foto_dir, "titel")
    kennung = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    ziel = os.path.join(ordner, f"{trail_id}-{kennung}.jpg")
    if os.path.exists(ziel):
        return ziel
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        if not art(r.content) or len(r.content) > MAX_UPLOAD_BYTES:
            return None
        daten = _jpeg(r.content, VORSCHAU_BREITE) if Image is not None else r.content
        os.makedirs(ordner, exist_ok=True)
        for alt in os.listdir(ordner):                  # veraltete Titelbilder dieses Trails
            if alt.startswith(f"{trail_id}-") and alt.endswith(".jpg"):
                os.remove(os.path.join(ordner, alt))
        _schreiben(ziel, daten)
        return ziel
    except (requests.RequestException, OSError, FotoError):
        return None
