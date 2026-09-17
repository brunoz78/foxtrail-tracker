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
                                          "ostschweiz/appenzell-foxtrail-go"]
    a = items[0]
    assert a["ort"] == "Saas-Fee" and a["name"] == "Allalin Maxi" and a["typ"] == "foxtrail"
    assert a["route"] == "Saas-Grund - Saas-Fee - Felskinn"
    assert a["bewertung"] == 4.6 and a["dauer"] == "3.5-4.5 Stunden" and a["preis"] == 36.0
    assert a["region"] == "wallis"
    assert a["url"] == "https://foxtrail.ch/produkte/trails/wallis/allalin-maxi/"
    b = items[1]
    assert b["bewertung"] is None and b["preis"] == 32.0 and b["ort"] == "Lausanne"
    g = items[2]
    assert g["typ"] == "go" and g["ort"] == "Appenzell" and g["name"] == "Appenzell – Foxtrail GO" and g["route"] == ""
    assert g["preis"] == 19.0 and g["region"] == "ostschweiz"


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
