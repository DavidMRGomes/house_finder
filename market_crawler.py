#!/usr/bin/env python3
"""Crawl publicly exposed Lisbon ordinary-sale listings."""
from __future__ import annotations

import argparse
import json
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
    for name, crawler in (("CustoJusto", crawl_custojusto), ("OLX", crawl_olx)):
        try:
            found = crawler(session); listings.extend(found); statuses.append({"source": name, "listings": len(found), "error": ""})
        except requests.RequestException as error:
            statuses.append({"source": name, "listings": 0, "error": str(error)})
    db.init_db(args.db)
    with db.connect(args.db) as conn:
        for item in listings:
            db.upsert_listing(conn, item, listing_type="market")
        for status in statuses:
            db.upsert_source(conn, status["source"], next((x["url"] for x in listings if x["source"] == status["source"]), ""), "market", "Ordinary-sale market listing source.", listing_type="market")
            db.upsert_source_status(conn, status["source"], "market", status["listings"], status["error"])
    print(f"Wrote {len(listings)} market listings to {args.db}")

if __name__ == "__main__":
    main()
