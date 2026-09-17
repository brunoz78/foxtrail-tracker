# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxtrail import scraper  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def test_parse_fixture():
    html = open(os.path.join(HERE, "fixture_page.html"), encoding="utf-8").read()
    items, has_next = scraper.parse_page(html)
    assert has_next
    assert [t["slug"] for t in items] == ["wallis/allalin-maxi", "westschweiz/alti",
                                          "ostschweiz/appenzell-foxtrail-go",
                                          "zuerich-und-umgebung/baccara-mini"]
    a = items[0]
    assert a["ort"] == "Saas-Fee" and a["name"] == "Allalin Maxi" and a["typ"] == "maxi"
    assert a["route"] == "Saas-Grund - Saas-Fee - Felskinn"
    assert a["bewertung"] == 4.6 and a["dauer"] == "3.5-4.5 Stunden" and a["preis"] == 36.0
    assert a["region"] == "wallis"
    assert a["url"] == "https://foxtrail.ch/produkte/trails/wallis/allalin-maxi/"
    b = items[1]
    assert b["bewertung"] is None and b["preis"] == 32.0 and b["ort"] == "Lausanne"
    assert b["typ"] == "foxtrail"
    g = items[2]
    assert g["typ"] == "go" and g["ort"] == "Appenzell" and g["name"] == "Appenzell – Foxtrail GO" and g["route"] == ""
    assert g["preis"] == 19.0 and g["region"] == "ostschweiz"
    m = items[3]
    assert m["typ"] == "mini" and m["ort"] == "Rapperswil" and m["dauer"] == "1-2 Stunden"
    assert m["preis"] == 36.0 and m["region"] == "zuerich-und-umgebung"


def test_schwierigkeits_filter_und_next():
    html = open(os.path.join(HERE, "fixture_page.html"), encoding="utf-8").read()
    assert scraper.parse_difficulty_filter(html) == {"einfach": "1304", "mittel": "1305", "schwierig": "1306"}
    assert scraper.next_page_url(html) == "https://foxtrail.ch/kategorie/trails/page/2/"
    assert scraper.parse_difficulty_filter("<html></html>") == {}
    assert scraper.next_page_url("<html></html>") is None


def test_fetch_difficulty(monkeypatch):
    # Zwei gefilterte Durchlaeufe (einfach: 2 Seiten, mittel: 1 Seite), Zeus in beiden -> mittel
    first = open(os.path.join(HERE, "fixture_page.html"), encoding="utf-8").read()
    def card(slug):
        return (f"<li class='product product_cat-trails product_cat-x'><a class='woocommerce-LoopProduct-link' "
                f"href='https://foxtrail.ch/produkte/trails/{slug}/'></a><div class='product-tag-badge'>"
                f"<a>O</a><a>N</a></div><div class='woocommerce-loop-product__title'>N</div></li>")
    pages = {
        "B/?filters=difficulty%5B1304%5D": f"<ul>{card('a/zeus')}{card('a/anda')}</ul>"
                                           "<a class='next page-numbers' href='B/page/2/?filters=difficulty%5B1304%5D'>→</a>",
        "B/page/2/?filters=difficulty%5B1304%5D": f"<ul>{card('a/emma')}</ul>",
        "B/?filters=difficulty%5B1305%5D": f"<ul>{card('a/zeus')}{card('a/aquae')}</ul>",
        "B/?filters=difficulty%5B1306%5D": "<ul></ul>",
    }
    monkeypatch.setattr(scraper, "fetch_page", lambda url, session=None: pages[url])
    assert scraper.fetch_difficulty("B", first, None, 0) == {
        "a/zeus": "mittel", "a/anda": "einfach", "a/emma": "einfach", "a/aquae": "mittel"}
    # Netzfehler: bisher Gesammeltes zurueckgeben statt abbrechen
    def kaputt(url, session=None):
        if "1305" in url:
            raise scraper.requests.ConnectionError("weg")
        return pages[url]
    monkeypatch.setattr(scraper, "fetch_page", kaputt)
    assert scraper.fetch_difficulty("B", first, None, 0) == {"a/zeus": "einfach", "a/anda": "einfach", "a/emma": "einfach"}


def test_parse_last_page():
    html = "<ul class='products'><li class='product product_cat-trails product_cat-jura'>" \
           "<a class='woocommerce-LoopProduct-link' href='https://foxtrail.ch/produkte/trails/jura/x/'></a>" \
           "<div class='product-tag-badge'><a>Ort</a><a>X</a></div>" \
           "<div class='woocommerce-loop-product__title'>X</div></li></ul>" \
           "<nav class='woocommerce-pagination'><a class='prev page-numbers'>←</a></nav>"
    items, has_next = scraper.parse_page(html)
    assert len(items) == 1 and not has_next
    assert items[0]["preis"] is None and items[0]["dauer"] == ""


def test_parse_empty():
    assert scraper.parse_page("<html></html>") == ([], False)
