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
    if "lisboa" not in str(area).casefold() and "lisboa" not in location.casefold():
        return None
    return {
        "source": source,
        "title": product.get("name", url.rsplit("/", 1)[-1].replace("-", " ")),
        "address": str(area),
        "municipality": "Lisboa",
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
            "source": "CustoJusto",
            "title": product.get("name", url.rsplit("/", 1)[-1].replace("-", " ")),
            "address": "Lisboa",
            "municipality": "Lisboa",
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
                "source": "OLX",
                "title": offer.get("name", "Imóvel em Lisboa"),
                "address": area_name,
                "municipality": area_name,
                "published_price_eur": offer.get("price"),
                "published_at": product.get("datePosted") or product.get("datePublished") or "",
                "url": urljoin(root, offer.get("url", "")),
                "image_url": images[0] if isinstance(images, list) and images else "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
            })
    return listings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="path to the SQLite database")
    args = parser.parse_args()
    session = requests.Session(); session.headers.update(HEADERS)
    listings, statuses = [], []
    crawlers = (
        ("CustoJusto", crawl_custojusto, "https://www.custojusto.pt/portugal/imobiliario"),
        ("OLX", crawl_olx, "https://www.olx.pt/imoveis/"),
        ("Imovirtual", lambda s: crawl_jsonld_portal(s, "Imovirtual", "https://www.imovirtual.com/pt/resultados/comprar/casa/lisboa", "Imovirtual"), "https://www.imovirtual.com/pt/resultados/comprar/casa/lisboa"),
        ("Century 21 Portugal", lambda s: crawl_jsonld_portal(s, "Century 21 Portugal", "https://www.century21.pt/comprar", "Century 21 Portugal"), "https://www.century21.pt/comprar"),
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
    for source in configured:
        if source["name"] in known_statuses:
            continue
        try:
            response = session.get(source["url"], timeout=30)
            error = "no dedicated public listing adapter" if response.ok else f"HTTP {response.status_code}"
        except requests.RequestException as error:
            error = str(error)
        statuses.append({"source": source["name"], "url": source["url"], "listings": 0, "error": error})
    with db.connect(args.db) as conn:
        for item in listings:
            db.upsert_listing(conn, item, listing_type="market")
        for status in statuses:
            db.upsert_source(conn, status["source"], next((x["url"] for x in listings if x["source"] == status["source"]), ""), "market", "Ordinary-sale market listing source.", listing_type="market")
            db.upsert_source_status(conn, status["source"], "market", status["listings"], status["error"])
    print(f"Wrote {len(listings)} market listings to {args.db}")

if __name__ == "__main__":
    main()
