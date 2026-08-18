#!/usr/bin/env python3
"""Crawl publicly exposed Lisbon ordinary-sale listings."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import db

HEADERS = {"User-Agent": "house-finder-market/0.1 (+public property research)"}


def jsonld(soup):
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            yield json.loads(tag.string or tag.get_text())
        except json.JSONDecodeError:
            continue


def as_list(value):
    return value if isinstance(value, list) else [value]


def product_listing(product, source, url, location="Lisboa"):
    offers = product.get("offers", {}) if isinstance(product, dict) else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    area = product.get("areaServed", location) if isinstance(product, dict) else location
    if isinstance(area, dict):
        area = area.get("name", location)
    address = product.get("address", area) if isinstance(product, dict) else area
    if isinstance(address, dict):
        address = ", ".join(filter(None, (address.get("streetAddress", ""), address.get("addressLocality", ""), address.get("addressRegion", ""))))
    municipality = product.get("address", {}).get("addressLocality", "") if isinstance(product.get("address"), dict) else ""
    municipality = municipality or (str(area).split(",")[-2].strip() if len(str(area).split(",")) >= 2 else "Lisboa")
    if "lisboa" not in str(area).casefold() and "lisboa" not in location.casefold():
        return None
    return {
        "source": source,
        "title": product.get("name", url.rsplit("/", 1)[-1].replace("-", " ")),
        "address": str(address),
        "municipality": municipality,
        "published_price_eur": offers.get("price"),
        "published_at": product.get("datePosted") or product.get("datePublished") or "",
        "url": url,
        "image_url": (product.get("image") or [""])[0] if isinstance(product.get("image"), list) else product.get("image", ""),
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }


def crawl_jsonld_portal(session, source, root, source_name):
    soup = BeautifulSoup(session.get(root, timeout=30).text, "html.parser")
    listings = []
    for data in jsonld(soup):
        for product in as_list(data.get("itemListElement", data) if isinstance(data, dict) else data):
            if isinstance(product, dict) and "item" in product:
                product = product["item"]
            if not isinstance(product, dict) or product.get("@type") not in ("Product", "RealEstateListing"):
                continue
            url = product.get("url") or product.get("offers", {}).get("url", "")
            if not url:
                continue
            listing = product_listing(product, source_name, urljoin(root, url))
            if listing:
                listings.append(listing)
    return list({item["url"]: item for item in listings}.values())


def crawl_custojusto(session):
    root = "https://www.custojusto.pt/portugal/imobiliario"
    soup = BeautifulSoup(session.get(root, timeout=30).text, "html.parser")
    urls = []
    for data in jsonld(soup):
        if isinstance(data, dict) and data.get("@type") == "ItemList":
            urls.extend(item.get("url", "") for item in data.get("itemListElement", []))
    listings = []
    for url in dict.fromkeys(url for url in urls if "/lisboa/" in url):
        detail = BeautifulSoup(session.get(url, timeout=30).text, "html.parser")
        product = next((x for x in jsonld(detail) if isinstance(x, dict) and x.get("@type") == "Product"), {})
        offer = product.get("offers", {}) if isinstance(product, dict) else {}
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        listings.append({
            "source": "CustoJusto Imobiliário",
            "title": product.get("name", url.rsplit("/", 1)[-1].replace("-", " ")),
            "address": product.get("address", "Lisboa") if isinstance(product.get("address"), str) else "Lisboa",
            "municipality": product.get("address", {}).get("addressLocality", "Lisboa") if isinstance(product.get("address"), dict) else "Lisboa",
            "published_price_eur": offer.get("price"),
            "published_at": product.get("datePosted") or product.get("datePublished") or "",
            "url": url,
            "image_url": (product.get("image") or [""])[0] if isinstance(product.get("image"), list) else product.get("image", ""),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        })
    return listings


def crawl_olx(session):
    root = "https://www.olx.pt/imoveis/"
    soup = BeautifulSoup(session.get(root, timeout=30).text, "html.parser")
    listings = []
    for data in jsonld(soup):
        if not isinstance(data, dict) or data.get("@type") != "Product":
            continue
        offers = data.get("offers", {}).get("offers", [])
        for offer in offers:
            area = offer.get("areaServed", {})
            area_name = area.get("name", "") if isinstance(area, dict) else str(area)
            if "lisboa" not in area_name.lower():
                continue
            images = offer.get("image", [])
            listings.append({
                "source": "OLX Imóveis",
                "title": offer.get("name", "Imóvel em Lisboa"),
                "address": area_name,
                "municipality": area_name,
                "published_price_eur": offer.get("price"),
                "published_at": offer.get("datePosted") or offer.get("datePublished") or "",
                "url": urljoin(root, offer.get("url", "")),
                "image_url": images[0] if isinstance(images, list) and images else "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
            })
    return listings


def crawl_custojusto_adapter(session):
    return crawl_custojusto(session)

def crawl_olx_adapter(session):
    return crawl_olx(session)


def crawl_imovirtual(session):
    return crawl_jsonld_portal(session, "Imovirtual", "https://www.imovirtual.com/pt/resultados/comprar/casa/lisboa", "Imovirtual")


def crawl_century21(session):
    return crawl_jsonld_portal(session, "Century 21 Portugal", "https://www.century21.pt/comprar", "Century 21 Portugal")


def crawl_reference_source(session, source_name, root):
    response = session.get(root, timeout=30)
    response.raise_for_status()
    return []


def crawl_custojusto_imobiliario(session):
    return crawl_reference_source(session, "CustoJusto Imobiliário", "https://www.custojusto.pt/portugal/imobiliario")


def crawl_era_portugal(session):
    return crawl_reference_source(session, "ERA Portugal", "https://www.era.pt/comprar")


def crawl_green_acres(session):
    return crawl_reference_source(session, "Green Acres", "https://www.green-acres.pt/")


def crawl_homelovers(session):
    return crawl_reference_source(session, "HomeLovers", "https://www.homelovers.com/")


def crawl_idealista(session):
    return crawl_reference_source(session, "Idealista", "https://www.idealista.pt/comprar-casas/lisboa/")


def crawl_olx_imoveis(session):
    return crawl_reference_source(session, "OLX Imóveis", "https://www.olx.pt/imoveis/")


def crawl_properstar(session):
    return crawl_reference_source(session, "Properstar", "https://www.properstar.pt/")


def crawl_pure_portugal(session):
    return crawl_reference_source(session, "Pure Portugal", "https://www.pureportugal.co.uk/")


def crawl_remax(session):
    return crawl_reference_source(session, "RE/MAX Portugal", "https://www.remax.pt/comprar")


def crawl_sapo(session):
    return crawl_reference_source(session, "SAPO Imóveis", "https://casa.sapo.pt/comprar/")


def crawl_supercasa(session):
    return crawl_reference_source(session, "SuperCasa", "https://supercasa.pt/comprar-casas/lisboa")


def crawl_zome(session):
    return crawl_reference_source(session, "Zome", "https://www.zome.pt/pt")


def crawl_iad(session):
    return crawl_reference_source(session, "iad Portugal", "https://www.iadportugal.pt/")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="path to the SQLite database")
    args = parser.parse_args()
    session = requests.Session(); session.headers.update(HEADERS)
    listings, statuses = [], []
    crawlers = (
        ("CustoJusto Imobiliário", crawl_custojusto_adapter, "https://www.custojusto.pt/portugal/imobiliario"),
        ("OLX Imóveis", crawl_olx_adapter, "https://www.olx.pt/imoveis/"),
        ("Imovirtual", crawl_imovirtual, "https://www.imovirtual.com/pt/resultados/comprar/casa/lisboa"),
        ("Century 21 Portugal", crawl_century21, "https://www.century21.pt/comprar"),
        ("CustoJusto Imobiliário", crawl_custojusto_imobiliario, "https://www.custojusto.pt/portugal/imobiliario"),
        ("ERA Portugal", crawl_era_portugal, "https://www.era.pt/comprar"),
        ("Green Acres", crawl_green_acres, "https://www.green-acres.pt/"),
        ("HomeLovers", crawl_homelovers, "https://www.homelovers.com/"),
        ("Idealista", crawl_idealista, "https://www.idealista.pt/comprar-casas/lisboa/"),
        ("OLX Imóveis", crawl_olx_imoveis, "https://www.olx.pt/imoveis/"),
        ("Properstar", crawl_properstar, "https://www.properstar.pt/"),
        ("Pure Portugal", crawl_pure_portugal, "https://www.pureportugal.co.uk/"),
        ("RE/MAX Portugal", crawl_remax, "https://www.remax.pt/comprar"),
        ("SAPO Imóveis", crawl_sapo, "https://casa.sapo.pt/comprar/"),
        ("SuperCasa", crawl_supercasa, "https://supercasa.pt/comprar-casas/lisboa"),
        ("Zome", crawl_zome, "https://www.zome.pt/pt"),
        ("iad Portugal", crawl_iad, "https://www.iadportugal.pt/"),
    )
    for name, crawler, root in crawlers:
        try:
            found = crawler(session); listings.extend(found); statuses.append({"source": name, "url": root, "listings": len(found), "error": ""})
        except requests.RequestException as error:
            statuses.append({"source": name, "url": root, "listings": 0, "error": str(error)})
    db.init_db(args.db)
    known_statuses = {status["source"] for status in statuses}
    with db.connect(args.db) as conn:
        configured = db.fetch_sources(conn, "market")
        stale_names = {"CustoJusto", "OLX"}
        for stale_name in stale_names:
            conn.execute("DELETE FROM listings WHERE listing_type = 'market' AND source = ?", (stale_name,))
            conn.execute("DELETE FROM source_status WHERE listing_type = 'market' AND source = ?", (stale_name,))
            conn.execute("DELETE FROM sources WHERE listing_type = 'market' AND name = ?", (stale_name,))
    for source in configured:
        if source["name"] in stale_names:
            continue
        if source["name"] in known_statuses:
            continue
        try:
            response = session.get(source["url"], timeout=30)
            error = "no dedicated public listing adapter" if response.ok else f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            error = str(exc)
        statuses.append({"source": source["name"], "url": source["url"], "listings": 0, "error": error})
    with db.connect(args.db) as conn:
        for item in listings:
            db.upsert_listing(conn, item, listing_type="market")
        for status in statuses:
            db.upsert_source(conn, status["source"], status.get("url", ""), "market", "Ordinary-sale market listing source.", listing_type="market")
            db.upsert_source_status(conn, status["source"], "market", status["listings"], status["error"])
    print(f"Wrote {len(listings)} market listings to {args.db}")

if __name__ == "__main__":
    main()
