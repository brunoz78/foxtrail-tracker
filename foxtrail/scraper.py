# -*- coding: utf-8 -*-
"""
Scraper fuer die oeffentliche Trail-Uebersicht auf foxtrail.ch.

Die Seite ist ein WooCommerce-Shop (Theme "Shoptimizer"). Je Trail ein
<li class="product ... product_cat-<region> ..."> mit
  a.woocommerce-LoopProduct-link[href]        -> URL, daraus der Slug
  .product-tag-badge a                        -> [Ort, Name]  (bei "Foxtrail GO" nur
                                                 ["Digitale Schnitzeljagd"])
  .woocommerce-loop-product__title            -> Name
  h3.woocommerce-loop-product__custom_title   -> Route
  .star-rating[aria-label="Rated 4.61 out of 5"]
  .trail-meta > span (ohne Klasse)            -> Dauer, z. B. "2-3 Stunden"
  .trail-price                                -> "CHF 32.00"
Paginierung: a.next.page-numbers existiert, solange eine weitere Seite folgt.

fetch_all() liefert eine Liste von dicts mit den Feldern
  slug, ort, name, route, typ ('foxtrail'|'mini'|'maxi'|'go'), region, bewertung, dauer, preis, url
Typ: GO am Badge, Mini/Maxi am Namen (trails.typ_aus_name) - das MINI/MAXI-Badge auf
der Website ist nur ins Titelbild gezeichnet, im HTML gibt es kein Element dafuer.
parse_page() ist rein (kein Netz) und damit offline testbar.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from . import config
from .trails import typ_aus_name

_SLUG_RE = re.compile(r"/produkte/trails/([^/]+/[^/]+)/?$")
_RATING_RE = re.compile(r"([\d.]+)")
_PRICE_RE = re.compile(r"([\d]+(?:[.,]\d+)?)")
GO_BADGE = "Digitale Schnitzeljagd"


class ScrapeError(Exception):
    pass


def _text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def parse_page(html):
    """(trails, has_next) aus dem HTML einer Kategorie-Seite."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.product"):
        link = li.select_one("a.woocommerce-LoopProduct-link")
        href = link.get("href", "") if link else ""
        m = _SLUG_RE.search(href)
        if not m:
            continue
        slug = m.group(1)
        region = next((c[len("product_cat-"):] for c in li.get("class", [])
                       if c.startswith("product_cat-") and c != "product_cat-trails"), "")
        badge = [_text(a) for a in li.select(".product-tag-badge a")]
        title = _text(li.select_one(".woocommerce-loop-product__title"))
        route = _text(li.select_one("h3.woocommerce-loop-product__custom_title"))
        is_go = bool(badge and badge[0] == GO_BADGE)
        if is_go:
            ort, name = title.split(" – ")[0].strip(), title
            if route == title:          # bei GO-Trails steht dort nur der Titel nochmals
                route = ""
        else:
            ort = badge[0] if badge else ""
            name = title or (badge[1] if len(badge) > 1 else slug)
        typ = typ_aus_name(name, go=is_go)
        rating = None
        sr = li.select_one(".star-rating")
        if sr:
            rm = _RATING_RE.search(sr.get("aria-label", "") or "")
            if rm:
                try:
                    rating = round(float(rm.group(1)), 1)
                except ValueError:
                    rating = None
        dauer = ""
        for sp in li.select(".trail-meta > span"):
            if not sp.get("class"):
                dauer = _text(sp)
                break
        preis = None
        pm = _PRICE_RE.search(_text(li.select_one(".trail-price")).replace("’", "").replace("'", ""))
        if pm:
            preis = float(pm.group(1).replace(",", "."))
        out.append({
            "slug": slug, "ort": ort, "name": name, "route": route, "typ": typ,
            "region": region, "bewertung": rating, "dauer": dauer, "preis": preis,
            "url": href if href.startswith("http") else f"https://foxtrail.ch/produkte/trails/{slug}/",
        })
    has_next = soup.select_one("a.next.page-numbers") is not None
    return out, has_next


def fetch_page(url, session=None):
    s = session or requests.Session()
    r = s.get(url, headers={"User-Agent": config.USER_AGENT, "Accept-Language": "de-CH,de;q=0.9"},
              timeout=30)
    r.raise_for_status()
    return r.text


def fetch_all(base_url=None, max_pages=50, delay=None):
    """Alle Seiten der Kategorie abrufen. Wirft ScrapeError, wenn nichts gefunden wurde."""
    base = (base_url or config.LIST_URL).rstrip("/")
    delay = config.SCRAPER_DELAY if delay is None else delay
    sess = requests.Session()
    trails, seen = [], set()
    for page in range(1, max_pages + 1):
        url = f"{base}/" if page == 1 else f"{base}/page/{page}/"
        try:
            html = fetch_page(url, sess)
        except requests.HTTPError as ex:
            if page > 1 and ex.response is not None and ex.response.status_code == 404:
                break
            raise ScrapeError(f"HTTP-Fehler bei {url}: {ex}") from ex
        except requests.RequestException as ex:
            raise ScrapeError(f"Abruf von {url} fehlgeschlagen: {ex}") from ex
        items, has_next = parse_page(html)
        for t in items:
            if t["slug"] not in seen:
                seen.add(t["slug"])
                trails.append(t)
        if not items or not has_next:
            break
        if delay:
            time.sleep(delay)
    if not trails:
        raise ScrapeError("Keine Trails gefunden - hat sich die Seitenstruktur geaendert?")
    return trails
