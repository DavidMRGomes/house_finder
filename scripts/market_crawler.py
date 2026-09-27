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

# The 16 concelhos that make up distrito Lisboa; used to recognize a reliable concelho match.
LISBON_DISTRICT_CONCELHOS = {
    "Alenquer", "Amadora", "Arruda dos Vinhos", "Azambuja", "Cadaval", "Cascais",
    "Lisboa", "Loures", "Lourinhã", "Mafra", "Odivelas", "Oeiras", "Sintra",
    "Sobral de Monte Agraço", "Torres Vedras", "Vila Franca de Xira",
}
# Other district capitals/major cities; used as a safety net to discard leaked out-of-district results.
OTHER_DISTRICT_CITY_MARKERS = {
    "porto", "braga", "aveiro", "coimbra", "faro", "setubal", "setúbal", "santarem", "santarém",
    "leiria", "viseu", "guarda", "castelo branco", "portalegre", "evora", "évora", "beja",
    "vila real", "braganca", "bragança", "viana do castelo", "funchal", "ponta delgada",
}


def parse_area(value):
    match = re.search(r"([\d][\d.,]*)", value or "")
    if not match:
        return None
    text = match.group(1)
    text = text.replace(".", "").replace(",", ".") if "," in text else text
    try:
        return float(text)
    except ValueError:
        return None


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
        text = " | ".join(detail.stripped_strings)
        location = re.search(r"Localiza[cç][aã]o \| ([^|]+) - ([^|]+) - ([^|]+)", text, re.I)
        distrito, concelho, freguesia = (part.strip() for part in location.groups()) if location else ("", "", "")
        if distrito and distrito.casefold() != "lisboa":
            continue
        typology = re.search(r"\bTipologia \| (T\d+)", text, re.I)
        area = re.search(r"[Áá]rea (?:bruta|[uú]til) \| ([\d.,]+)\s*m", text, re.I)
        listings.append({
            "source": "CustoJusto Imobiliário",
            "title": product.get("name", url.rsplit("/", 1)[-1].replace("-", " ")),
            "address": ", ".join(filter(None, (freguesia, concelho, distrito))) or "Lisboa",
            "distrito": distrito or "Lisboa",
            "municipality": concelho or "Lisboa",
            "freguesia": freguesia,
            "published_price_eur": offer.get("price"),
            "published_at": product.get("datePosted") or product.get("datePublished") or "",
            "url": url,
            "image_url": (product.get("image") or [""])[0] if isinstance(product.get("image"), list) else product.get("image", ""),
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "typology": typology.group(1).upper() if typology else "",
            "area_m2": parse_area(area.group(1)) if area else None,
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


def crawl_century21_api(session):
    # No public autocomplete endpoint was found to resolve other concelho ids, so this
    # adapter is scoped to concelho Lisboa (address id 1106) rather than the full distrito.
    root = "https://www.century21.pt/api/properties?address_names=Lisboa&addresses=1106&page={page}&ad_type=sell&order_by=entered_market_desc"
    results = []
    page = 1
    while True:
        response = session.get(root.format(page=page), timeout=30)
        response.raise_for_status()
        data = response.json()
        records = data.get("data", [])
        if not records:
            break
        for record in records:
            rooms = record.get("number_of_rooms")
            title = record.get("title", {})
            title = title.get("pt", "") if isinstance(title, dict) else str(title)
            results.append({
                "source": "Century 21 Portugal",
                "title": title or "Imóvel em Lisboa",
                "address": "Lisboa, Lisboa",
                "distrito": "Lisboa",
                "municipality": "Lisboa",
                "freguesia": "",
                "published_price_eur": record.get("price"),
                "published_at": record.get("entered_market", ""),
                "url": urljoin("https://www.century21.pt", record.get("link", "")),
                "image_url": (record.get("images") or [""])[0],
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": f"T{rooms}" if rooms is not None else "",
                "area_m2": record.get("gross_area") or record.get("useful_area"),
            })
        if page * 20 >= min(data.get("total", 0), 20 * 60):
            break
        page += 1
    return list({item["url"]: item for item in results if item["url"]}.values())


def crawl_remax(session):
    root = "https://www.remax.pt/_next/data/9NhcqVV_5tn3842MeY0T2/pt/comprar.json?locale=pt"
    payload = session.get(root, timeout=30).json()
    records = payload.get("pageProps", {}).get("properties", payload.get("properties", []))
    results = []
    for record in records or []:
        address = record.get("address", "")
        if isinstance(address, dict): address = ", ".join(str(x) for x in address.values())
        if "lisboa" not in str(address).casefold(): continue
        rooms = record.get("number_of_rooms")
        results.append({"source":"RE/MAX Portugal","title":record.get("title", "Imóvel em Lisboa"),"address":str(address),"municipality":"Lisboa","published_price_eur":record.get("price"),"published_at":"","url":urljoin("https://www.remax.pt", record.get("link", "")),"image_url":(record.get("images") or [""])[0],"last_seen":datetime.now(timezone.utc).isoformat(),"typology":f"T{rooms}" if rooms is not None else ""})
    return results


def crawl_iad(session):
    api = "https://www.iadportugal.pt/api/properties"
    base_params = [("serpSlug", "lisboa"), ("serpSlug", "venda"), ("serpSlug", "apartamento"), ("locale", "pt")]
    results = []
    page = 1
    while True:
        response = session.get(api, params=base_params + [("page", str(page))], timeout=30)
        response.raise_for_status()
        data = response.json()
        items = data.get("items", [])
        if not items:
            break
        for item in items:
            place = (item.get("location") or {}).get("place", "")
            if any(marker in place.casefold() for marker in OTHER_DISTRICT_CITY_MARKERS):
                continue
            concelho = place if place in LISBON_DISTRICT_CONCELHOS else ""
            freguesia = "" if concelho else place
            rooms = next((r.get("value") for r in item.get("rooms", []) if r.get("type") == "bedrooms"), None)
            area = next((s.get("value") for s in item.get("surfaceList", []) if s.get("type") == "gross-area"), None)
            slug = (item.get("slugs") or {}).get("pt", "")
            reference = item.get("propertyListingRef", "")
            results.append({
                "source": "iad Portugal",
                "title": item.get("title", "Imóvel em Lisboa"),
                "address": ", ".join(filter(None, (freguesia, concelho, "Lisboa"))),
                "distrito": "Lisboa",
                "municipality": concelho or "Lisboa",
                "freguesia": freguesia,
                "published_price_eur": (item.get("price") or {}).get("main"),
                "published_at": "",
                "url": f"https://www.iadportugal.pt/anuncio/{slug}/r{reference}" if slug and reference else "",
                "image_url": (item.get("photos") or [""])[0],
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": f"T{rooms}" if rooms is not None else "",
                "area_m2": area,
            })
        total_items = data.get("totalItems", 0)
        per_page = data.get("itemsPerPage") or 30
        if page * per_page >= total_items:
            break
        page += 1
    return list({item["url"]: item for item in results if item["url"]}.values())


def crawl_zome(session):
    root = "https://www.zome.pt/pt"
    soup = BeautifulSoup(session.get(root, timeout=30).text, "html.parser")
    results = []
    for link in soup.select('a[href*="ZMPT"]'):
        href = link.get("href", "")
        text = link.get_text(" ", strip=True)
        if "lisboa" not in (text + href).casefold(): continue
        match = re.search(r"\bT[0-9]+\b", text, re.I)
        results.append({"source":"Zome","title":text[:200],"address":text,"municipality":"Lisboa","published_price_eur":None,"published_at":"","url":urljoin(root, href),"image_url":"","last_seen":datetime.now(timezone.utc).isoformat(),"typology":match.group(0).upper() if match else ""})
    return list({x["url"]:x for x in results}.values())


def crawl_pure_portugal(session):
    root = "https://pureportugal.co.uk/"
    soup = BeautifulSoup(session.get(root, timeout=30).text, "html.parser")
    results = []
    for link in soup.select('a[href*="/property/"]'):
        href = link.get("href", "")
        text = link.get_text(" ", strip=True)
        if "lisboa" not in (text + href).casefold(): continue
        price = re.search(r"([\d.\s]+)\s*(?:€|EUR)", text)
        results.append({"source":"Pure Portugal","title":text[:200],"address":"Lisboa","municipality":"Lisboa","published_price_eur":parse_price(price.group(1)) if price else None,"published_at":"","url":urljoin(root, href),"image_url":"","last_seen":datetime.now(timezone.utc).isoformat()})
    return list({x["url"]:x for x in results}.values())


def crawl_homelovers(session):
    root = "https://homelovers.com/buyproperties?FilterDistrictId=2&filtroHome=true"
    response = session.get(root, timeout=30)
    response.encoding = response.apparent_encoding or "utf-8"  # server omits charset; page is UTF-8
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for link in soup.select('a[href*="/property"], a[href*="/imovel"], a[href*="a155"]'):
        href = link.get("href", "")
        text = link.parent.get_text(" ", strip=True)
        if "TO BUY" not in text:
            continue
        location = re.search(r"REF:\s*\S+\s+([^-]+?)\s*-\s*(.+?)\s+TO\s+BUY", text)
        concelho, freguesia = (part.strip() for part in location.groups()) if location else ("Lisboa", "")
        price = re.search(r"([\d.]+)\s*EUR", text)
        area = re.search(r"([\d.,]+)\s*m²", text)
        rooms = re.search(r"\b([0-9])\s+Quartos\b", text, re.I)
        results.append({
            "source": "HomeLovers",
            "title": link.get_text(" ", strip=True) or text[:160],
            "address": ", ".join(filter(None, (freguesia, concelho, "Lisboa"))),
            "distrito": "Lisboa",
            "municipality": concelho,
            "freguesia": freguesia,
            "published_price_eur": parse_price(price.group(1)) if price else None,
            "published_at": "",
            "url": urljoin(root, href),
            "image_url": "",
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "typology": f"T{rooms.group(1)}" if rooms else "",
            "area_m2": parse_area(area.group(1)) if area else None,
        })
    return list({x["url"]: x for x in results}.values())


def parse_price(value):
    return float(value.replace(".", "").replace(" ", "").replace("\xa0", "").replace(",", "."))


def crawl_reference_source(session, source_name, root):
    response = session.get(root, timeout=30)
    response.raise_for_status()
    return []


def crawl_custojusto_imobiliario(session):
    return crawl_reference_source(session, "CustoJusto Imobiliário", "https://www.custojusto.pt/portugal/imobiliario")


def crawl_era_portugal(session):
    root = "https://www.era.pt/comprar"
    page_response = session.get(root, timeout=30)
    token_match = re.search(r'__RequestVerificationToken[^>]*value="([^"]+)"', page_response.text)
    if not token_match:
        return []
    headers = {
        "requestverificationtoken": token_match.group(1),
        "moduleid": "410",
        "tabid": "36",
        "x-requested-with": "XMLHttpRequest",
        "content-type": "application/json",
        "referer": root,
    }
    results = []
    page = 1
    while True:
        payload = {
            "page": str(page),
            "propertiesTypeId": [1, 2],
            "onlyDevelopments": False,
            "order": "3",
            "isResidential": True,
            "nonResidential": False,
            "districts": ["11"],  # distrito Lisboa; no concelho restriction
            "businessTypeId": [1],  # Comprar (sale) only
        }
        response = session.post("https://www.era.pt/API/ServicesModule/Property/Search", json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        properties = data.get("PropertyList", [])
        if not properties:
            break
        for record in properties:
            gallery = record.get("Gallery") or []
            price_value = (record.get("SellPrice") or {}).get("Value", "")
            price_match = re.search(r"([\d][\d.\s\xa0]*)", price_value)
            # Localization is "Freguesia, Distrito"; Title is "Tipo / Concelho, Freguesia".
            freguesia, _, distrito = (record.get("Localization", "") or "").partition(",")
            freguesia, distrito = freguesia.strip(), distrito.strip()
            if distrito and distrito.casefold() != "lisboa":
                continue
            title_text = record.get("Title", "")
            concelho_match = re.search(r"/\s*([^,]+),", title_text)
            concelho = concelho_match.group(1).strip() if concelho_match else ""
            rooms = record.get("Rooms")
            typology_match = re.search(r"\bT[0-9]+\b", title_text, re.I)
            typology = typology_match.group(0).upper() if typology_match else (f"T{rooms}" if isinstance(rooms, int) else "")
            area = record.get("ListingArea") or record.get("NetArea")
            results.append({
                "source": "ERA Portugal",
                "title": title_text.strip() or "Imóvel em Lisboa",
                "address": ", ".join(filter(None, (freguesia, concelho, distrito or "Lisboa"))),
                "distrito": distrito or "Lisboa",
                "municipality": concelho or "Lisboa",
                "freguesia": freguesia,
                "published_price_eur": parse_price(price_match.group(1)) if price_match else None,
                "published_at": "",
                "url": record.get("DetailUrl", ""),
                "image_url": gallery[0].get("Url", "") if gallery else "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": typology,
                "area_m2": parse_area(area) if area else None,
            })
        if page >= min(data.get("TotalPages", page), 150):
            break
        page += 1
    return list({item["url"]: item for item in results if item["url"]}.values())


def crawl_green_acres(session):
    return crawl_reference_source(session, "Green Acres", "https://www.green-acres.pt/")


def crawl_idealista(session):
    return crawl_reference_source(session, "Idealista", "https://www.idealista.pt/comprar-casas/lisboa/")


def crawl_olx_imoveis(session):
    return crawl_reference_source(session, "OLX Imóveis", "https://www.olx.pt/imoveis/")


def crawl_properstar(session):
    return crawl_reference_source(session, "Properstar", "https://www.properstar.pt/")


def crawl_sapo(session):
    return crawl_reference_source(session, "SAPO Imóveis", "https://casa.sapo.pt/comprar/")


def crawl_supercasa(session):
    return crawl_reference_source(session, "SuperCasa", "https://supercasa.pt/comprar-casas/lisboa")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="path to the SQLite database")
    args = parser.parse_args()
    session = requests.Session(); session.headers.update(HEADERS)
    listings, statuses = [], []
    crawl_time = datetime.now(timezone.utc).isoformat()
    crawlers = (
        ("CustoJusto Imobiliário", crawl_custojusto_adapter, "https://www.custojusto.pt/portugal/imobiliario"),
        ("OLX Imóveis", crawl_olx_adapter, "https://www.olx.pt/imoveis/"),
        ("Imovirtual", crawl_imovirtual, "https://www.imovirtual.com/pt/resultados/comprar/casa/lisboa"),
        ("Century 21 Portugal", crawl_century21_api, "https://www.century21.pt/comprar"),
        ("ERA Portugal", crawl_era_portugal, "https://www.era.pt/comprar"),
        ("Green Acres", crawl_green_acres, "https://www.green-acres.pt/"),
        ("HomeLovers", crawl_homelovers, "https://homelovers.com/buyproperties?FilterDistrictId=2&filtroHome=true"),
        ("Idealista", crawl_idealista, "https://www.idealista.pt/comprar-casas/lisboa/"),
        ("Properstar", crawl_properstar, "https://www.properstar.pt/"),
        ("Pure Portugal", crawl_pure_portugal, "https://www.pureportugal.co.uk/"),
        ("RE/MAX Portugal", crawl_remax, "https://www.remax.pt/comprar"),
        ("SAPO Imóveis", crawl_sapo, "https://casa.sapo.pt/comprar/"),
        ("SuperCasa", crawl_supercasa, "https://supercasa.pt/comprar-casas/lisboa"),
        ("Zome", crawl_zome, "https://www.zome.pt/pt"),
        ("iad Portugal", crawl_iad, "https://www.iadportugal.pt/anuncios/lisboa/venda/apartamento"),
    )
    for name, crawler, root in crawlers:
        try:
            found = crawler(session); listings.extend(found); statuses.append({"source": name, "url": root, "listings": len(found), "error": ""})
        except requests.RequestException as error:
            statuses.append({"source": name, "url": root, "listings": 0, "error": str(error)})
        except Exception as error:
            statuses.append({"source": name, "url": root, "listings": 0, "error": f"adapter failed: {error}"})
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
            existing = conn.execute("SELECT url FROM listings WHERE url = ?", (item.get("url", ""),)).fetchone()
            db.upsert_listing(conn, item, listing_type="market")
            conn.execute("UPDATE listings SET first_seen = COALESCE(first_seen, ?), is_active = 1, removed_at = NULL WHERE url = ?", (crawl_time, item.get("url", "")))
            if existing is None:
                db.record_listing_event(conn, item, "new", crawl_time)
        for status in statuses:
            db.upsert_source(conn, status["source"], status.get("url", ""), "market", "Ordinary-sale market listing source.", listing_type="market")
            db.upsert_source_status(conn, status["source"], "market", status["listings"], status["error"])
            if not status["error"]:
                db.finalize_market_source(conn, status["source"], {item["url"] for item in listings if item["source"] == status["source"]}, crawl_time)
    print(f"Wrote {len(listings)} market listings to {args.db}")

if __name__ == "__main__":
    main()
