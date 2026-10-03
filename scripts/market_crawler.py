#!/usr/bin/env python3
"""Crawl publicly exposed Lisbon ordinary-sale listings."""
from __future__ import annotations

import argparse
import base64
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import db

HEADERS = {"User-Agent": "house-finder-market/0.1 (+public property research)"}


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
    root = "https://www.custojusto.pt/lisboa/imobiliario"
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
        if concelho and concelho.casefold() != "lisboa":
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
    response = session.get(root, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
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
    # The JSON-LD on search pages is generic site-wide SEO boilerplate, not the actual
    # filtered results, so the real listings are read from the embedded __NEXT_DATA__ props.
    # "lisboa/lisboa" is Imovirtual's distrito/concelho path; "lisboa" alone resolves to "todo-o-pais".
    root = "https://www.imovirtual.com/pt/resultados/comprar/apartamento/lisboa/lisboa"
    results = []
    page = 1
    while True:
        response = session.get(root, params={"page": page}, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        tag = soup.select_one('script#__NEXT_DATA__')
        if not tag:
            break
        data = json.loads(tag.string or tag.get_text())
        search_ads = data.get("props", {}).get("pageProps", {}).get("data", {}).get("searchAds", {})
        items = search_ads.get("items", [])
        if not items:
            break
        for item in items:
            slug = item.get("slug", "")
            if not slug:
                continue
            locations = ((item.get("location") or {}).get("reverseGeocoding") or {}).get("locations", [])
            by_level = {loc.get("locationLevel"): loc.get("name", "") for loc in locations}
            distrito = by_level.get("district", "Lisboa")
            concelho = by_level.get("council", "Lisboa")
            freguesia = by_level.get("parish", "")
            title = item.get("title", "")
            typology_match = re.search(r"\bT[0-9]+\b", title, re.I)
            results.append({
                "source": "Imovirtual",
                "title": title or "Imóvel em Lisboa",
                "address": ", ".join(filter(None, (freguesia, concelho, distrito))),
                "distrito": distrito,
                "municipality": concelho,
                "freguesia": freguesia,
                "published_price_eur": (item.get("totalPrice") or {}).get("value"),
                "published_at": item.get("dateCreated", ""),
                "url": f"https://www.imovirtual.com/pt/anuncio/{slug}",
                "image_url": ((item.get("images") or [{}])[0] or {}).get("medium", ""),
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": typology_match.group(0).upper() if typology_match else "",
                "area_m2": item.get("areaInSquareMeters"),
            })
        pagination = search_ads.get("pagination", {})
        if page >= pagination.get("totalPages", page):
            break
        page += 1
    return list({item["url"]: item for item in results}.values())


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
        if page * 20 >= data.get("total", 0):
            break
        page += 1
    return list({item["url"]: item for item in results if item["url"]}.values())


def crawl_remax(session):
    api = "https://remax.pt/api/Listing/PaginatedMultiMatchSearchWithGeoHash"
    # Region1ID 76 (distrito Lisboa) + Region2ID 537 scope this to concelho Lisboa only.
    filters = [
        {"field": "businessTypeID", "operationType": "int", "operator": "=", "value": "1", "label": "buy"},
        {"field": "Region1ID", "operationType": "string", "operator": "=", "value": "76"},
        {"field": "Region2ID", "operationType": "string", "operator": "=", "value": "537"},
        {"field": "listingClassID", "operationType": "int", "operator": "=", "value": "1"},
        {"field": "isSpecialExclusive", "operator": "=", "operationType": "string", "value": "false"},
    ]
    results = []
    page = 1
    page_size = 50
    while True:
        payload = {"filters": filters, "pageNumber": page, "pageSize": page_size, "sort": ["-PublishDate"], "searchValue": "Lisboa", "precision": 12}
        response = session.post(api, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        records = data.get("results", [])
        if not records:
            break
        for record in records:
            listing_id = record.get("listingTitle", "")
            if not listing_id:
                continue
            rooms = record.get("numberOfBedrooms")
            typology = f"T{rooms}" if rooms is not None else ""
            picture = record.get("listingPictureUrl", "")
            distrito = record.get("regionName1", "") or "Lisboa"
            concelho = record.get("regionName2", "")
            freguesia = record.get("regionName3", "")
            results.append({
                "source": "RE/MAX Portugal",
                "title": f"{typology} em {freguesia or concelho or 'Lisboa'}".strip() if typology else "Imóvel em Lisboa",
                "address": ", ".join(filter(None, (freguesia, concelho, distrito))),
                "distrito": distrito,
                "municipality": concelho or "Lisboa",
                "freguesia": freguesia,
                "published_price_eur": record.get("listingPrice"),
                "published_at": record.get("publishDate", ""),
                "url": f"https://remax.pt/pt/imoveis/x/{listing_id}",
                "image_url": f"https://i.maxwork.pt/ds-l/{picture}" if picture else "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": typology,
                "area_m2": record.get("totalArea") or record.get("livingArea") or record.get("builtArea"),
            })
        if page * page_size >= data.get("total", 0):
            break
        page += 1
    return list({item["url"]: item for item in results}.values())


def crawl_casayes(session):
    api = "https://casayes.pt/api/frontend/frontendlisting/SearchWithPagination"
    headers = {"tenantid": "7", "languageid": "9", "beedigital": "casayes", "device": "web"}
    # region1Id 27 / region2Id 179 scope this to concelho Lisboa; page sizes above 20 return nothing.
    filters = [
        {"field": "businessTypeId", "operationType": "int", "operator": "=", "value": "1"},
        {"field": "region1Id", "operationType": "int", "operator": "=", "value": "27"},
        {"field": "region2Id", "operationType": "int", "operator": "=", "value": "179"},
        {"field": "listingTypeId", "operationType": "multiple", "operator": "=", "value": "1,2,4,10"},
    ]
    results = []
    page = 1
    while page <= 600:
        response = session.post(api, headers=headers, json={"filters": filters, "pageNumber": page, "pageSize": 20, "sort": ["-PublishingDateDay"]}, timeout=30)
        response.raise_for_status()
        info = response.json()
        for record in info.get("items", []):
            listing_id = record.get("publicId", "")
            if not listing_id:
                continue
            rooms = record.get("numberOfBedrooms")
            typology = f"T{rooms}" if rooms is not None else ""
            concelho = record.get("regionName2", "") or "Lisboa"
            freguesia = record.get("regionName3", "")
            picture = record.get("defaultPictureUrl", "")
            results.append({
                "source": "Casayes",
                "title": f"{typology} em {freguesia or concelho}".strip() if typology else f"Imóvel em {freguesia or concelho}",
                "address": ", ".join(filter(None, (freguesia, concelho, "Lisboa"))),
                "distrito": "Lisboa",
                "municipality": concelho,
                "freguesia": freguesia,
                "published_price_eur": record.get("listingPrice"),
                "published_at": record.get("publishingDate", ""),
                "url": f"https://casayes.pt/pt/imovel/{record.get('seoUriDescription', 'x')}/{listing_id}",
                "image_url": f"https://i.casayes.pt/l-search/{picture}" if picture else "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": typology,
                "area_m2": record.get("totalArea") or None,
            })
        if not info.get("hasNextPage"):
            break
        page += 1
    return list({item["url"]: item for item in results}.values())


def crawl_jll(session):
    page_url = "https://residential.jll.pt/venda/venda/apartamento~moradia~moradia-geminada/lisboa?stt_not_in=7,106&srt=14&use_square_pag=1"
    html = session.get(page_url, timeout=30)
    html.raise_for_status()
    # The Ego Real Estate API rejects calls without the per-page token and request id embedded in the HTML.
    token = re.search(r'"APIToken":"([^"]+)"', html.text)
    request_id = re.search(r"'requestID':'([^']+)'", html.text)
    if not token or not request_id:
        raise ValueError("JLL page has no API token")
    headers = {"authorizationtoken": token.group(1), "Referer": page_url, "x-served-by": "JanelaDigital", "x-async": "true", "x-requestid": request_id.group(1), "userinfotoken": ""}
    params = {"restparams": "venda/apartamento~moradia~moradia-geminada/lisboa", "nre": "12", "stt_not_in": "7,106", "srt": "14", "use_square_pag": "1", "gather_attributes": "1", "lng": "pt-pt"}
    api = "https://websiteapi.egorealestate.com/v1/"
    options = session.get(api + "SearchOptions", params={"restparams": params["restparams"], "stt_not_in": "7,106", "searchfields": "Parish", "withData": "true", "filterType": "PropertyListFilter", "allowedInfo": "{}", "lng": "pt-pt"}, headers=headers, timeout=30)
    options.raise_for_status()
    parishes = [item["ID"] for item in options.json().get("Parish") or [] if item.get("ID")]
    results = []
    # The API stops after 35 pages (420 results) per query, so crawl one parish at a time.
    for parish in parishes:
        page = 1
        while page <= 35:
            response = session.get(api + "Properties", params=dict(params, parish=parish, pag=page), headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            records = data.get("Properties", [])
            if not records:
                break
            results.extend(jll_listing(record) for record in records if record.get("ID"))
            if page * 12 >= data.get("TotalRecords", 0):
                break
            page += 1
    return list({item["url"]: item for item in results}.values())


def jll_listing(record):
    listing_id = record["ID"]
    price = next((price.get("PriceValue") for business in record.get("PropertyBusiness", []) if business.get("BusinessID") == 1 for price in business.get("Prices", [])), None)
    rooms = record.get("Rooms")
    images = record.get("Images") or []
    slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", record.get("Title", "")).encode("ascii", "ignore").decode().lower()).strip("-")
    concelho = record.get("Municipality", "") or "Lisboa"
    freguesia = record.get("Parish", "")
    return {
        "source": "JLL Residential",
        "title": record.get("Title") or f"Imóvel em {freguesia or concelho}",
        "address": ", ".join(filter(None, (freguesia, concelho, record.get("District", "") or "Lisboa"))),
        "distrito": record.get("District", "") or "Lisboa",
        "municipality": concelho,
        "freguesia": freguesia,
        "published_price_eur": price or None,
        "published_at": "",
        "url": f"https://residential.jll.pt/imovel/{slug or 'x'}/{listing_id}",
        "image_url": (images[0].get("Thumbnail_640X480") or images[0].get("Thumbnail", "")) if images else record.get("Thumbnail", "") or "",
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "typology": f"T{rooms}" if rooms is not None else "",
        "area_m2": record.get("GrossArea") or record.get("NetArea") or None,
    }


def crawl_kw(session):
    api = "https://www.kwportugal.pt/api/portal/listProperties"
    body = {"pageNumber": 1, "idCurrency": 1, "idCulture": 1, "numRecords": 500, "filterType": 1, "idRegions1": "11", "idRegions2": "1106", "orderField": 11, "idBusinesses": "1,4,5", "filterDate": "null", "idEnergyClasses": []}
    slug = lambda text: re.sub(r"[^A-Za-z0-9]+", "-", unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()).strip("-")
    results = []
    page = 1
    while page <= 20:
        response = session.post(api, json=dict(body, pageNumber=page), timeout=60)
        response.raise_for_status()
        records = response.json()
        for record in records:
            # The unfiltered search also returns shops, garages, land and whole buildings.
            if record.get("idBusiness") != 1 or record.get("type") not in ("Apartamento", "Moradia"):
                continue
            concelho = record.get("region2", "") or "Lisboa"
            freguesia = record.get("region3", "")
            typology = record.get("typology") or ""
            results.append({
                "source": "Keller Williams Portugal",
                "title": record.get("designation") or f"{record['type']} em {freguesia or concelho}",
                "address": ", ".join(filter(None, (freguesia, concelho, record.get("region1", "") or "Lisboa"))),
                "distrito": record.get("region1", "") or "Lisboa",
                "municipality": concelho,
                "freguesia": freguesia,
                "published_price_eur": record.get("price") or None,
                "published_at": "",
                "url": f"https://www.kwportugal.pt/pt/Imovel/Venda/{slug(record['type'])}/{slug(record.get('region1'))}/{slug(concelho)}/{slug(freguesia)}/{record['idProperty']}",
                "image_url": record.get("defaultImageUrl") or "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": typology,
                "area_m2": record.get("totalArea") or record.get("livingArea") or None,
            })
        if len(records) < body["numRecords"]:
            break
        page += 1
    return list({item["url"]: item for item in results}.values())


def crawl_engel_volkers(session):
    search_url = "https://www.engelvoelkers.com/pt/pt/propertysearch"
    base = [("businessArea[]", "residential"), ("currency", "EUR"), ("measurementSystem", "metric"), ("placeIds[]", "ChIJO_PkYRozGQ0R0DaQ5L3rAAQ"), ("placeName", "Lisboa"), ("propertyMarketingType[]", "sale"), ("propertyTypeSubType.apartment[]", ""), ("propertyTypeSubType.house[]", ""), ("searchMode", "classic"), ("searchRadius", "0"), ("sortingOptionId", "PRICE_ASC")]
    # The apartment/house filter still returns shops, parking and business transfers.
    not_home = re.compile(r"estacionamento|garagem|parqueamento|trespasse|\bloja\b|espaço comercial|restaurante|armazém|escritório|terreno|\blote\b", re.I)
    is_home = re.compile(r"\bT\d\b|apartamento|moradia|vivenda|villa", re.I)
    results, seen = {}, set()
    page = 1
    while page <= 100:
        response = session.get(search_url, params=base + [("page", page)], timeout=30)
        response.raise_for_status()
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.S)
        if not match:
            raise ValueError("Engel & Völkers page has no embedded listing data")
        data = next((q["state"]["data"] for q in json.loads(match.group(1))["props"]["pageProps"]["dehydratedState"]["queries"] if isinstance(q["state"].get("data"), dict) and "listings" in q["state"]["data"]), None)
        if data is None:
            raise ValueError("Engel & Völkers page has no listings query")
        records = [item["listing"] for item in data["listings"]]
        # Pages past the end repeat the last page.
        if not records or all(record["id"] in seen for record in records):
            break
        seen.update(record["id"] for record in records)
        for record in records:
            title = record.get("profile", {}).get("title", "")
            rooms = (record.get("rooms") or {}).get("min")
            price = ((record.get("price") or {}).get("salesPrice") or {}).get("min")
            if rooms is None or (not_home.search(title) and not is_home.search(title)):
                continue
            neighborhood = next((c["text"] for c in record.get("addressComponents", []) if c.get("placeType") == "neighborhood"), "") or record.get("neighborhoodOverwrite") or ""
            area = record.get("area") or {}
            images = record.get("uploadCareImageIds") or []
            # Total rooms include the living room, so bedrooms (tipologia) are one fewer.
            typology = f"T{max(rooms - 1, 0)}"
            results[record["id"]] = {
                "source": "Engel & Völkers",
                "title": title or f"{typology} em {neighborhood or 'Lisboa'}",
                "address": ", ".join(filter(None, (neighborhood, "Lisboa", "Lisboa"))),
                "distrito": "Lisboa",
                "municipality": "Lisboa",
                "freguesia": neighborhood,
                "published_price_eur": price or None,
                "published_at": "",
                "url": f"https://www.engelvoelkers.com/pt/pt/exposes/{record['id']}",
                "image_url": f"https://uploadcare.engelvoelkers.com/{images[0]}/-/format/webp/-/resize/640x/" if images else "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": typology,
                "area_m2": (area.get("totalSurface") or {}).get("min") or (area.get("livingSurface") or {}).get("min") or None,
            }
        if len(seen) >= data.get("listingsTotal", 0):
            break
        page += 1
    return list(results.values())


def crawl_savills(session):
    base = "https://search.savills.com/pt/pt/lista?SearchList=Id_844+Category_TownVillageCity&Tenure=GRS_T_B&SortOrder=SO_PCDD&Currency=EUR&PropertyTypes=GRS_PT_H,GRS_PT_APT,GRS_PT_ND,GRS_PT_PENT&Bedrooms=-1&Bathrooms=-1&CarSpaces=-1&Receptions=-1&ResidentialSizeUnit=SquareMeter&CommercialSizeUnit=SquareMeter&LandAreaUnit=Hectare&SaleableAreaUnit=SquareMeter&AvailableSizeUnit=SquareMeter&Category=GRS_CAT_RES&Shapes=W10"
    results = {}
    page = 1
    while page <= 50:
        response = session.get(f"{base}&CurrentPage={page}", timeout=30)
        response.raise_for_status()
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.S)
        if not match:
            raise ValueError("Savills page has no embedded listing data")
        state = json.loads(match.group(1))["props"]["initialReduxState"]
        list_page = state["listPage"]["pageMap"].get(str(page))
        if not list_page:
            break
        for property_id in list_page["results"]["Properties"]:
            record = state["properties"].get(property_id)
            if not record:
                continue
            if re.match(r"Pr[eé]dio", record.get("AddressLine1") or "", re.I):
                continue
            parts = [part.strip() for part in (record.get("AddressLine2") or "").split(",") if part.strip()]
            concelho = parts[-1] if parts else "Lisboa"
            freguesia = parts[0] if len(parts) > 1 else ""
            gallery = record.get("PropertyCardImagesGallery") or []
            bedrooms = record.get("Bedrooms")
            # New developments are priced on request and list a range of bedrooms.
            is_development = record.get("IsParent") or record.get("IsNewDevelopment")
            size = re.search(r"\d[\d.]*", record.get("SizeFormatted") or "")
            area = float(size.group(0).replace(".", "")) if size else (record.get("Size") or {}).get("SqMt") or None
            results[property_id] = {
                "source": "Savills",
                "title": record.get("AddressLine1") or f"Imóvel em {freguesia or concelho}",
                "address": ", ".join(filter(None, (freguesia, concelho, "Lisboa"))),
                "distrito": "Lisboa",
                "municipality": concelho,
                "freguesia": freguesia,
                "published_price_eur": record.get("Price") if record.get("ShowPrice") and record.get("Price") else None,
                "published_at": "",
                "url": f"https://search.savills.com/pt/pt/imovel-pormenor/{record.get('ExternalPropertyIDFormatted') or property_id.lower()}",
                "image_url": (gallery[0].get("ImageUrl_M") or gallery[0].get("ImageUrl_L") or "") if gallery else "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": f"T{bedrooms}" if bedrooms is not None and not is_development else "",
                "area_m2": area,
            }
        if page >= list_page["paging"].get("total", 1):
            break
        page += 1
    return list(results.values())


def crawl_iad(session):
    api = "https://www.iadportugal.pt/api/properties"
    # "lisboa-1106" is iad's concelho Lisboa slug, distinct from the "lisboa" distrito-wide slug.
    base_params = [("serpSlug", "lisboa-1106"), ("serpSlug", "venda"), ("serpSlug", "apartamento"), ("locale", "pt")]
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
            location = item.get("location") or {}
            postcode = location.get("postcode", "")
            if postcode and not postcode.startswith("1"):
                continue  # drop the occasional mis-geocoded listing outside concelho Lisboa
            place = location.get("place", "")
            freguesia = "" if place.casefold() == "lisboa" else place
            rooms = next((r.get("value") for r in item.get("rooms", []) if r.get("type") == "bedrooms"), None)
            area = next((s.get("value") for s in item.get("surfaceList", []) if s.get("type") == "gross-area"), None)
            slug = (item.get("slugs") or {}).get("pt", "")
            reference = item.get("propertyListingRef", "")
            results.append({
                "source": "iad Portugal",
                "title": item.get("title", "Imóvel em Lisboa"),
                "address": ", ".join(filter(None, (freguesia, "Lisboa"))),
                "distrito": "Lisboa",
                "municipality": "Lisboa",
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
    # Zome's site is a client-rendered SPA backed by a public Supabase project; this is the
    # same "publishable" (anon-level) key shipped to every browser, not a private credential.
    api = "https://luvskhnljpxllkxpeasu.supabase.co/rest/v1/rpc/get_angariacoes"
    headers = {
        "apikey": "sb_publishable_fY6BgFcFONgcOhMf1Snqjw_qwwJY2zk",
        "authorization": "Bearer sb_publishable_fY6BgFcFONgcOhMf1Snqjw_qwwJY2zk",
        "content-profile": "pt_prod",
        "content-type": "application/json",
    }
    base_payload = {
        "localizationiso": "PT", "typebusiness": 1, "typelisting": None, "localizacao": None,
        "typologylisting": None, "arraylocalization": [2021], "minprecoimovel": None, "maxprecoimovel": None,
        "areaminlisting": None, "statuslisting": None, "attr_piscina": None, "attr_elevador": None,
        "attr_garagem": None, "attr_parqueamento": None, "attr_mobilidadereduzida": None,
        "valorentradafinanciamento": None, "prazoamortizacaofinanciamento": None, "taxafixafinanciamento": None,
        "spread": None, "pricebymonth": None, "arrayzmid": None, "mylocalizacao": None, "mylocalizacaodistance": None,
        "idconsultor": None, "moradahubconsultorid": None, "orderby": "dataentradarede", "orderdirection": "DESC",
    }
    results = []
    offset = 0
    limit = 50
    while True:
        response = session.post(api, json={**base_payload, "limiti": limit, "offseti": offset}, headers=headers, timeout=30)
        response.raise_for_status()
        records = response.json()
        if not records:
            break
        for record in records:
            pid = record.get("pid", "")
            slug = json.loads(record.get("url_detail_view_link") or "{}").get("PT", "")
            if not pid or not slug:
                continue
            price_digits = re.sub(r"[^\d]", "", record.get("precoimovel") or "")
            prop_type = json.loads(record.get("tipoimovel") or "{}").get("PT", "")
            typology = json.loads(record.get("tipologiaimovel") or "{}").get("PT", "")
            results.append({
                "source": "Zome",
                "title": f"{prop_type} {typology}".strip() or "Imóvel em Lisboa",
                "address": ", ".join(filter(None, (record.get("localizacaolevel3imovel", ""), record.get("localizacaolevel2imovel", ""), record.get("localizacaolevel1imovel", "")))),
                "distrito": record.get("localizacaolevel1imovel", "") or "Lisboa",
                "municipality": record.get("localizacaolevel2imovel", "") or "Lisboa",
                "freguesia": record.get("localizacaolevel3imovel", ""),
                "published_price_eur": float(price_digits) if price_digits else None,
                "published_at": record.get("dataentradarede", ""),
                "url": f"https://www.zome.pt/pt/{slug}",
                "image_url": ((record.get("gallery") or {}).get("mres") or [""])[0],
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": typology,
                "area_m2": record.get("areabrutaconst") or record.get("areautilhab"),
            })
        if len(records) < limit:
            break
        offset += limit
    return list({item["url"]: item for item in results if item["url"]}.values())


def crawl_pure_portugal(session):
    root = "https://pureportugal.co.uk/properties/"
    # cat is type+type+type+district+type+type-; slot 4 (97) is the "Lisbon" district filter.
    # Built as a raw query string because the literal "+" separators must not be percent-encoded.
    url = root + "?cat=54+54+54+97+54+54-&landmin=0&landmax=0&order=ASC&v="
    response = session.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for card in soup.select("div.card"):
        link = card.select_one('a[href*="/property/"]')
        if not link:
            continue
        url = link.get("href", "").strip()
        title_el = card.select_one("h1")
        title = title_el.get_text(strip=True) if title_el else "Imóvel em Lisboa"
        inline_blocks = card.select("div.inline")
        price_digits = re.sub(r"[^\d]", "", inline_blocks[0].get_text()) if inline_blocks else ""
        concelho, distrito = "", "Lisbon"
        if len(inline_blocks) > 1:
            bolds = inline_blocks[1].select("b")
            if bolds:
                concelho = bolds[0].get_text(strip=True)
            if len(bolds) > 1:
                distrito = bolds[1].get_text(strip=True)
        results.append({
            "source": "Pure Portugal",
            "title": title,
            "address": ", ".join(filter(None, (concelho, distrito))),
            "distrito": "Lisboa",
            "municipality": concelho or "Lisboa",
            "freguesia": "",
            "published_price_eur": float(price_digits) if price_digits else None,
            "published_at": "",
            "url": url,
            "image_url": "",
            "last_seen": datetime.now(timezone.utc).isoformat(),
        })
    return list({item["url"]: item for item in results if item["url"]}.values())


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
            "districts": ["11"],  # distrito Lisboa; concelho Lisboa is filtered client-side below
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
            if concelho.casefold() != "lisboa":
                continue
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
        if page >= data.get("TotalPages", page):
            break
        page += 1
    return list({item["url"]: item for item in results if item["url"]}.values())


def crawl_green_acres(session):
    root = "https://www.green-acres.pt/property-for-sale/lisboa"
    response = session.get(root, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    # This SEO landing page only surfaces a handful of "latest" picks for the wider Lisbon
    # region (no full paginated search was found), so listing counts here stay small.
    for card in soup.select("div.announce-card[data-o]"):
        try:
            url = base64.b64decode(card["data-o"]).decode()
        except (ValueError, KeyError):
            continue
        text = card.get_text(" | ", strip=True)
        price_el = card.select_one(".info-price")
        price_digits = re.sub(r"[^\d]", "", price_el.get_text()) if price_el else ""
        location = re.search(r"([^|]+?)\s*\(([^)]+)\)", text)
        freguesia, concelho = (part.strip() for part in location.groups()) if location else ("", "")
        area = re.search(r"([\d.,]+)\s*m²(?!\s*of land)", text)
        rooms = re.search(r"(\d+)\s*bedrooms?", text, re.I)
        title = f"{f'T{rooms.group(1)} ' if rooms else ''}em {freguesia or concelho or 'Lisboa'}".strip()
        results.append({
            "source": "Green Acres",
            "title": title,
            "address": ", ".join(filter(None, (freguesia, concelho, "Lisboa"))),
            "distrito": "Lisboa",
            "municipality": concelho or "Lisboa",
            "freguesia": freguesia,
            "published_price_eur": float(price_digits) if price_digits else None,
            "published_at": "",
            "url": url,
            "image_url": "",
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "typology": f"T{rooms.group(1)}" if rooms else "",
            "area_m2": parse_area(area.group(1)) if area else None,
        })
    return list({item["url"]: item for item in results if item["url"]}.values())


def crawl_idealista(session):
    return crawl_reference_source(session, "Idealista", "https://www.idealista.pt/comprar-casas/lisboa/")


def crawl_olx_imoveis(session):
    return crawl_reference_source(session, "OLX Imóveis", "https://www.olx.pt/imoveis/")


def crawl_properstar(session):
    return crawl_reference_source(session, "Properstar", "https://www.properstar.pt/")


def parse_sapo_cards(html):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for link in soup.select("a.property-info"):
        # Result links go through a click tracker; the real listing URL is its l= parameter.
        target = re.search(r"[?&]l=([^&]+)", link.get("href", "").replace("&amp;", "&"))
        if not target:
            continue
        url = target.group(1).split("?")[0].split("#")[0]
        card = link.find_parent(lambda tag: tag.select_one(".property-photos") is not None)
        image = card.select_one(".property-photos img[data-src], .property-photos img[src^=http]") if card else None
        title = link.get("title", "").removeprefix("Ver ") or link.select_one(".property-type").get_text(strip=True)
        parts = [part.strip() for part in link.select_one(".property-location").get_text().split(",") if part.strip()]
        distrito = parts.pop().removeprefix("Distrito de ") if parts and parts[-1].startswith("Distrito de") else "Lisboa"
        concelho = parts[-1] if parts else "Lisboa"
        freguesia = parts[-2] if len(parts) > 1 else ""
        price = re.search(r"\d[\d.]*", link.select_one(".property-price-value").get_text() if link.select_one(".property-price-value") else "")
        area = re.search(r"([\d.,]+)\s*m", link.select_one(".property-features-text").get_text() if link.select_one(".property-features-text") else "")
        typology = re.search(r"\bT\d+\b", link.select_one(".property-type").get_text())
        listings.append({
            "source": "SAPO Imóveis",
            "title": title,
            "address": ", ".join(filter(None, (freguesia, concelho, distrito))),
            "distrito": distrito,
            "municipality": concelho,
            "freguesia": freguesia,
            "published_price_eur": float(price.group(0).replace(".", "")) if price else None,
            "published_at": "",
            "url": url,
            "image_url": (image.get("data-src") or image.get("src") or "") if image else "",
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "typology": typology.group(0) if typology else "",
            "area_m2": parse_area(area.group(1)) if area else None,
        })
    return listings


def crawl_sapo(session):
    root = "https://casa.sapo.pt/comprar-apartamentos/lisboa/"
    results = {}
    page = 1
    while page <= 250:
        # CASA SAPO answers 429 to bursts, so crawl slowly and fail the source rather than return a partial result.
        response = session.get(root, params={"pn": page} if page > 1 else None, timeout=30)
        response.raise_for_status()
        found = parse_sapo_cards(response.text)
        if not found or all(item["url"] in results for item in found):
            break
        results.update({item["url"]: item for item in found})
        page += 1
        time.sleep(4)
    return list(results.values())


def crawl_supercasa(session):
    return crawl_reference_source(session, "SuperCasa", "https://supercasa.pt/comprar-casas/lisboa")


def crawl_imobancos(session):
    results = []
    page = 1
    while True:
        response = session.get(f"https://www.imobancos.pt/en/imoveis/Lisboa/page/{page}", timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select('a.group.block[id^="property-"]')
        if not cards:
            break
        for card in cards:
            pin = card.select_one('span[class*="map-pin"]')
            loc_span = pin.find_parent("div").select_one("span.truncate") if pin else None
            location = loc_span.get_text(strip=True) if loc_span else ""
            parts = [part.strip() for part in location.split(",")]
            freguesia = parts[0] if len(parts) >= 3 else ""
            concelho = parts[-2] if len(parts) >= 2 else ""
            distrito = parts[-1] if parts else "Lisboa"
            if concelho.casefold() != "lisboa":
                continue
            type_el = card.select_one("span.text-xs.font-medium")
            prop_type = type_el.get_text(strip=True) if type_el else ""
            if prop_type.casefold() not in ("apartamento", "moradia"):
                continue
            title_el = card.select_one("h3")
            title = title_el.get_text(strip=True) if title_el else "Imóvel em Lisboa"
            area_icon = card.select_one('span[class*="square-3-stack"]')
            area_text = area_icon.find_parent("div").get_text(strip=True) if area_icon else ""
            area_match = re.search(r"([\d.,]+)", area_text)
            typology_icon = card.select_one('span[class*="home-20-solid"]')
            typology = typology_icon.find_parent("div").get_text(strip=True) if typology_icon else ""
            price_el = card.select_one("div.text-right span")
            price_digits = re.sub(r"[^\d]", "", price_el.get_text()) if price_el else ""
            image_el = card.select_one("img")
            results.append({
                "source": "Imobancos",
                "title": title,
                "address": ", ".join(filter(None, (freguesia, concelho, distrito))),
                "distrito": distrito or "Lisboa",
                "municipality": concelho,
                "freguesia": freguesia,
                "published_price_eur": float(price_digits) if price_digits else None,
                "published_at": "",
                "url": urljoin("https://www.imobancos.pt", card.get("href", "").split("#")[0]),
                "image_url": image_el.get("src", "") if image_el else "",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "typology": typology,
                "area_m2": parse_area(area_match.group(1)) if area_match else None,
            })
        page += 1
    return list({item["url"]: item for item in results if item["url"]}.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="path to the SQLite database")
    args = parser.parse_args()
    session = requests.Session(); session.headers.update(HEADERS)
    listings, statuses = [], []
    crawl_time = datetime.now(timezone.utc).isoformat()
    crawlers = (
        ("CustoJusto Imobiliário", crawl_custojusto_adapter, "https://www.custojusto.pt/portugal/imobiliario"),
        ("OLX Imóveis", crawl_olx_adapter, "https://www.olx.pt/imoveis/"), # Not Working
        ("Imobancos", crawl_imobancos, "https://www.imobancos.pt/en/imoveis/Lisboa/page/1"),
        ("Imovirtual", crawl_imovirtual, "https://www.imovirtual.com/pt/resultados/comprar/casa/lisboa"),
        ("Casayes", crawl_casayes, "https://casayes.pt/pt/comprar/casaseapartamentos/lisboa/lisboa"), # Not Working
        ("Century 21 Portugal", crawl_century21_api, "https://www.century21.pt/comprar"),
        ("Engel & Völkers", crawl_engel_volkers, "https://www.engelvoelkers.com/pt/pt/propertysearch?businessArea[]=residential&placeName=Lisboa&propertyMarketingType[]=sale"),
        ("ERA Portugal", crawl_era_portugal, "https://www.era.pt/comprar"),
        ("Green Acres", crawl_green_acres, "https://www.green-acres.pt/"),
        ("HomeLovers", crawl_homelovers, "https://homelovers.com/buyproperties?FilterDistrictId=2&filtroHome=true"),
        ("Idealista", crawl_idealista, "https://www.idealista.pt/comprar-casas/lisboa/"), # Not Working
        ("Properstar", crawl_properstar, "https://www.properstar.pt/"), # Not Working
        ("Pure Portugal", crawl_pure_portugal, "https://www.pureportugal.co.uk/"),
        ("RE/MAX Portugal", crawl_remax, "https://remax.pt/pt/comprar/imoveis/habitacao/lisboa/r/r/t?s=%7B%22rg%22%3A%22Lisboa%22%2C%22cd%22%3A%2239.38219%3B-9.6255807%3B38.60869%3B-8.6568063%22%2C%22mio%22%3A%22true%22%7D&p=1&o=-PublishDate"),
        ("Savills", crawl_savills, "https://search.savills.com/pt/pt/lista?SearchList=Id_844+Category_TownVillageCity&Tenure=GRS_T_B"),
        ("SAPO Imóveis", crawl_sapo, "https://casa.sapo.pt/comprar/"), # Not Working
        ("SuperCasa", crawl_supercasa, "https://supercasa.pt/comprar-casas/lisboa"), # Not Working
        ("Zome", crawl_zome, "https://www.zome.pt/pt"),
        ("iad Portugal", crawl_iad, "https://www.iadportugal.pt/anuncios/lisboa/venda/apartamento"),
        ("JLL Residential", crawl_jll, "https://residential.jll.pt/venda/venda/apartamento~moradia~moradia-geminada/lisboa"),
        ("Keller Williams Portugal", crawl_kw, "https://www.kwportugal.pt/pt/imoveis?business=1&district=11&council=1106"),
    )
    for name, crawler, root in crawlers:
        try:
            found = crawler(session); listings.extend(found); statuses.append({"source": name, "url": root, "listings": len(found), "error": ""})
            print(f"Crawled {name} got {len(found)} listings")
        except requests.RequestException as error:
            statuses.append({"source": name, "url": root, "listings": 0, "error": str(error)})
            print(f"Error crawling {name}: {str(error)}")
        except Exception as error:
            statuses.append({"source": name, "url": root, "listings": 0, "error": f"adapter failed: {error}"})
            print(f"Error crawling {name}: {str(error)}")
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
            # Re-posted ads get a new URL/ID; inherit first_seen from an earlier ad with the same source, title and address.
            twin = None
            if existing is None and item.get("title") and item.get("address"):
                twin = conn.execute(
                    "SELECT MIN(first_seen) AS first_seen FROM listings WHERE listing_type = 'market' AND source = ? AND title = ? AND address = ? AND first_seen IS NOT NULL",
                    (item.get("source", ""), item["title"], item["address"]),
                ).fetchone()
            db.upsert_listing(conn, item, listing_type="market")
            first_seen = (twin["first_seen"] if twin and twin["first_seen"] else None) or crawl_time
            conn.execute("UPDATE listings SET first_seen = COALESCE(first_seen, ?), is_active = 1, removed_at = NULL WHERE url = ?", (first_seen, item.get("url", "")))
            if existing is None and first_seen == crawl_time:
                db.record_listing_event(conn, item, "new", crawl_time)
        for status in statuses:
            db.upsert_source(conn, status["source"], status.get("url", ""), "market", "Ordinary-sale market listing source.", listing_type="market")
            db.upsert_source_status(conn, status["source"], "market", status["listings"], status["error"])
            if not status["error"]:
                db.finalize_market_source(conn, status["source"], {item["url"] for item in listings if item["source"] == status["source"]}, crawl_time)
    
    print(f"Wrote {len(listings)} market listings to {args.db}")
    
    # Print summary of listings per source
    print("\nCrawling Summary:")
    print("-" * 50)
    for status in statuses:
        if status["listings"] > 0:
            print(f"Crawled {status['source']}: {status['listings']} listings")
        else:
            print(f"Crawled {status['source']}: 0 listings (Error: {status['error']})")


if __name__ == "__main__":
    main()
