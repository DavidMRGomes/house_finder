#!/usr/bin/env python3
"""Crawl public Portuguese property-auction sources for Lisbon listings."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

import db

USER_AGENT = "house-finder/0.2 (+public-auction-research; respectful crawling)"
LISBON_DISTRICT_ID = "13"
TAX_BROWSER_PROFILE = Path(__file__).parent.parent / ".portal-das-financas-browser"
TAX_LOGIN_URL = "https://www.acesso.gov.pt/v2/loginForm?partID=SIVI&path=/vendasat/lista/vendas"
TAX_SALES_URL = "https://vendas.portaldasfinancas.gov.pt/vendasat/lista/vendas"

@dataclass(frozen=True)
class Source:
    name: str
    url: str
    category: str
    notes: str

@dataclass
class Listing:
    source: str
    title: str
    address: str = ""
    municipality: str = ""
    freguesia: str = ""
    current_bid_eur: float | None = None
    minimum_bid_eur: float | None = None
    published_price_eur: float | None = None
    auction_date: str = ""
    url: str = ""
    last_seen: str = ""
    image_url: str = ""

SOURCES = (
    Source("e-leiloes", "https://www.e-leiloes.pt/", "judicial", "Official electronic judicial auctions; public API adapter."),
    Source("Leiloatrium", "https://leiloatrium.pt/", "auctioneer", "Public judicial-sales auctioneer."),
    Source("OneFix", "https://www.onefix-leiloeiros.pt/tipo_verbas/1/Imoveis", "auctioneer", "Public property auction lots."),
    Source("Santander Imoveis", "https://imoveis.santander.pt", "bank", "Public bank property portal; not all listings are auctions."),
    Source("Seguranca Social", "https://www.seg-social-patrimonio.pt/comprar/imoveis/default.aspx", "government", "Public Social Security property sales portal."),
    Source("Portal das Financas", TAX_SALES_URL, "tax", "Tax authority SIVI property sales; requires authentication."),
    Source("Citius", "https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx", "judicial", "Public judicial-sales search form; queried per court since it requires a court to be selected."),
    Source("Leilosoc", "https://www.leilosoc.com/category/5-imovel/", "auctioneer", "Public property lots."),
    Source("Euro Estates", "https://www.euroestates.pt/realestate/auctions", "auctioneer", "Public active-auctions search."),
    Source("Caixa Imobiliario", "https://www.caixaimobiliario.pt/pt/comprar?q=Lisboa", "bank", "Public Caixa Imobiliario Lisbon property search."),
    Source("Millennium BCP Imoveis", "https://ind.millenniumbcp.pt/pt/Particulares/viver/Imoveis/Pages/imoveis.aspx#/default.aspx", "bank", "Public Millennium BCP property portal."),
    Source("Bankinter Imoveis", "https://www.bankinter.pt/credito-habitacao/portal-imoveis-bankinter", "bank", "Bankinter property portal; current public route returns HTTP 403."),
    Source("Montepio Imoveis", "https://imoveisbancomontepio.pt/Comprar/Lisboa", "bank", "Public Montepio Lisbon property search."),
    Source("Imobancos", "https://imobancos.pt/imoveis/page/1", "bank", "Public aggregator of bank-owned property portfolios; filtered to Apartamento/Moradia for sale."),
)

class Crawler:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.5"})
        self.status: list[dict[str, str | int]] = []

    def get(self, url: str, **kwargs: object) -> requests.Response:
        response = self.session.get(url, timeout=30, **kwargs)
        response.raise_for_status()
        return response

    def record(self, source: Source, count: int = 0, error: str = "") -> None:
        self.status.append({"source": source.name, "url": source.url, "listings": count, "error": error})

    def leilosoc(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Leilosoc")
        try:
            soup = BeautifulSoup(self.get(source.url).text, "html.parser")
            urls = {urljoin(source.url, link["href"]) for link in soup.select('a[href*="/lot/"]')}
            results = []
            for url in sorted(urls):
                try:
                    listing = self.leilosoc_lot(url)
                    if listing:
                        results.append(listing)
                except requests.RequestException as error:
                    print(f"warning: Leilosoc lot {url}: {error}", file=sys.stderr)
            self.record(source, len(results))
            return results
        except requests.RequestException as error:
            self.record(source, error=str(error))
            return []

    def leilosoc_lot(self, url: str) -> Listing | None:
        soup = BeautifulSoup(self.get(url).text, "html.parser")
        text = " | ".join(soup.stripped_strings)
        title = re.search(r"(?:^|\| )((?:Moradia|Apartamento|Casa|Prédio|Terreno)[^|]+)", text, re.I)
        location = re.search(r"Localização \| ([^|]+) \| ([^|]+)", text, re.I)
        minimum = re.search(r"Valor Mínimo \| ([^|]+)", text, re.I)
        if not title or not location or "lisboa" not in location.group(1).lower():
            return None
        return Listing("Leilosoc", title.group(1).strip(), location.group(2).strip(), location.group(1).strip(), minimum_bid_eur=parse_euro_amount(minimum.group(1)) if minimum else None, url=url, last_seen=now())

    def euro_estates(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Euro Estates")
        search_url = "https://www.euroestates.pt/realestate/search"
        payload = {"district_id": LISBON_DISTRICT_ID, "businesstype_id": "1", "price_slider": "250,1490001", "area_slider": "9,2294"}
        try:
            response = self.session.post(search_url, data=payload, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            urls = {urljoin(search_url, link["href"]) for link in soup.select('a[href*="/realestate/view/"]')}
            results = []
            for url in sorted(urls):
                try:
                    listing = self.euro_estates_detail(url)
                    if listing:
                        results.append(listing)
                except requests.RequestException as error:
                    print(f"warning: Euro Estates lot {url}: {error}", file=sys.stderr)
            self.record(source, len(results))
            return results
        except requests.RequestException as error:
            self.record(source, error=str(error))
            return []

    def euro_estates_detail(self, url: str) -> Listing | None:
        soup = BeautifulSoup(self.get(url).text, "html.parser")
        text = " | ".join(soup.stripped_strings)
        reference = re.search(r"Referência:\s*([^|]+)", text, re.I)
        location = re.search(r"-\s*,?\s*(Lisboa[^|]*)", text, re.I)
        price = re.search(r"(?:Venda|Preço)\s*\|?\s*([\d.\s]+,\d{2}\s*€)", text, re.I)
        if not location or not reference:
            return None
        return Listing("Euro Estates", reference.group(1).strip(), location.group(1).strip(" -"), "Lisboa", published_price_eur=parse_euro_amount(price.group(1)) if price else None, url=url, last_seen=now())

    def e_leiloes(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "e-leiloes")
        results: list[Listing] = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto("https://www.e-leiloes.pt/index.aspx", wait_until="networkidle", timeout=90000)
                table_params = {
                    "first": 0,
                    "rows": 100,
                    "sortField": "dataFim",
                    "sortOrder": 1,
                    "filters": {"palavrasChave": {"value": "Lisboa", "matchMode": "contains"}},
                }
                endpoint = "/api/Eventos/?tableParams=" + quote(json.dumps(table_params, separators=(",", ":")))
                records = page.evaluate("async path => (await (await fetch(path)).json()).list", endpoint)
                for record in records:
                    if record.get("tipoId") != 1 or record.get("moradaDistrito") != "Lisboa":
                        continue
                    reference = record.get("referencia", str(record.get("id", "")))
                    results.append(Listing(
                        source="e-leiloes",
                        title=record.get("titulo", "").strip(),
                        address=", ".join(filter(None, (record.get("moradaFreguesia"), record.get("moradaConcelho"), record.get("moradaDistrito")))),
                        municipality=record.get("moradaConcelho", ""),
                        current_bid_eur=record.get("lanceAtual") or None,
                        minimum_bid_eur=record.get("valorMinimo") or None,
                        published_price_eur=record.get("valorBase") or None,
                        auction_date=record.get("dataFim", ""),
                        url="https://www.e-leiloes.pt/eventos?palavrasChave=" + quote(reference),
                        last_seen=now(),
                        image_url="https://www.e-leiloes.pt/api/" + str(record.get("capa", "")),
                    ))
                browser.close()
            self.record(source, len(results))
        except Exception as error:
            self.record(source, error=f"browser/API adapter failed: {error}")
        return results

    def leiloatrium(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Leiloatrium")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(source.url, wait_until="domcontentloaded", timeout=60000)
                text = page.locator("body").inner_text()
                browser.close()
            lisboa_count = re.search(r"Lisboa\s*\((\d+)\)", text, re.I)
            count = int(lisboa_count.group(1)) if lisboa_count else 0
            self.record(source, count, "no public Lisbon lots currently listed" if count == 0 else "source exposes Lisbon lots but needs detail adapter")
        except Exception as error:
            self.record(source, error=str(error))
        return []

    def onefix(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "OneFix")
        results: list[Listing] = []
        try:
            soup = BeautifulSoup(self.get(source.url).content.decode("utf-8", "replace"), "html.parser")
            urls = {urljoin(source.url, a["href"]) for a in soup.select('a[href*="/verba/"]') if "lisboa" in a.get_text(" ", strip=True).lower() or "lisboa" in a["href"].lower()}
            for url in sorted(urls):
                detail = BeautifulSoup(self.get(url).content.decode("utf-8", "replace"), "html.parser")
                text = " | ".join(detail.stripped_strings)
                title = next(iter(detail.select("h1, h2")), None)
                title_text = title.get_text(" ", strip=True) if title else url.rsplit("/", 1)[-1].replace("_", " ")
                minimum = re.search(r"Valor mínimo de Venda:\s*\|?\s*([\d\s.]+,\d{2}\s*€)", text, re.I)
                current = re.search(r"Valor última licitação:\s*\|?\s*([\d\s.]+,\d{2}\s*€)", text, re.I)
                base = re.search(r"Valor Base:\s*\|?\s*([\d\s.]+,\d{2}\s*€)", text, re.I)
                end = re.search(r"Termina em:\s*\|?\s*([^|]+)", text, re.I)
                results.append(Listing("OneFix", title_text, "Lisboa", "Lisboa", current_bid_eur=parse_euro_amount(current.group(1)) if current else None, minimum_bid_eur=parse_euro_amount(minimum.group(1)) if minimum else None, published_price_eur=parse_euro_amount(base.group(1)) if base else None, auction_date=end.group(1).strip() if end else "", url=url, last_seen=now()))
            self.record(source, len(results))
        except requests.RequestException as error:
            self.record(source, error=str(error))
        return results

    def santander_imoveis(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Santander Imoveis")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36", locale="pt-PT")
                page.goto(source.url, wait_until="networkidle", timeout=60000)
                page.get_by_role("button", name="Pesquisar").click()
                page.wait_for_load_state("networkidle", timeout=60000)
                results = self.santander_parse_results(BeautifulSoup(page.content(), "html.parser"), page.url)
                browser.close()
            self.record(source, len(results), "Lisbon filter unavailable on source; retained returned records mentioning Lisboa" if not results else "")
            return results
        except Exception as error:
            self.record(source, error=f"browser adapter failed: {error}")
            return []

    def santander_parse_results(self, soup: BeautifulSoup, page_url: str) -> list[Listing]:
        listings: list[Listing] = []
        for link in soup.select('a[href*="/imoveis/"]'):
            href = link.get("href", "")
            text = link.get_text(" ", strip=True)
            card = link.find_parent(class_=re.compile(r"card|property|imovel", re.I)) or link.parent
            card_text = card.get_text(" ", strip=True)
            combined = " ".join(part for part in (text, card_text) if part)
            if "lisboa" not in combined.lower():
                continue
            price = re.search(r"([\d.\s]+(?:,\d{1,2})?)\s*€", combined)
            image = card.select_one("img[src], img[data-src]") if card else None
            listings.append(Listing("Santander Imoveis", text or card_text[:160], card_text, "Lisboa", published_price_eur=parse_euro_amount(price.group(1)) if price else None, url=urljoin(page_url, href), last_seen=now(), image_url=(image.get("data-src") or image.get("src") or "") if image else ""))
        return listings

    def seguranca_social(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Seguranca Social")
        try:
            soup = BeautifulSoup(self.get(source.url).text, "html.parser")
            form = soup.select_one('form[action*="default.aspx"]')
            if not form:
                raise ValueError("public property search form not found")
            action = urljoin(source.url, form.get("action", ""))
            payload = {field.get("name"): field.get("value", "") for field in form.select('input[type="hidden"][name]')}
            payload.update({"cboTipoImovelPesquisa": "", "cboTipoNegocioPesquisa": "1", "cboDistritoPesquisa": "14"})
            response = self.session.post(action, data=payload, timeout=30)
            response.raise_for_status()
            results = self.seguranca_social_parse_results(BeautifulSoup(response.text, "html.parser"), response.url)
            self.record(source, len(results), "Lisbon property search returned no records" if not results else "")
            return results
        except (requests.RequestException, ValueError) as error:
            self.record(source, error=str(error))
            return []

    def seguranca_social_parse_results(self, soup: BeautifulSoup, page_url: str) -> list[Listing]:
        listings: list[Listing] = []
        for link in soup.select('a[href]'):
            href = link.get("href", "")
            text = link.get_text(" ", strip=True)
            if not href or not any(term in href.lower() for term in ("imovel", "ficha", "detalhe")):
                continue
            card = link.find_parent(class_=re.compile(r"card|imovel|property|item", re.I)) or link.parent
            card_text = card.get_text(" ", strip=True)
            combined = " ".join(part for part in (text, card_text) if part)
            if "lisboa" not in combined.lower():
                continue
            price = re.search(r"([\d.\s]+(?:,\d{1,2})?)\s*€", combined)
            image = card.select_one("img[src], img[data-src]") if card else None
            listings.append(Listing("Seguranca Social", text or card_text[:160], card_text, "Lisboa", published_price_eur=parse_euro_amount(price.group(1)) if price else None, url=urljoin(page_url, href), last_seen=now(), image_url=(image.get("data-src") or image.get("src") or "") if image else ""))
        return listings

    def millennium_imoveis(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Millennium BCP Imoveis")
        base = "https://millenniumimoveis.janeladigital.com"
        search_pages = ("/Search.aspx", "/Search.aspx?tab=329", "/Search.aspx?tab=204", "/Search.aspx?tab=313&toptab=204", "/Search.aspx?tab=201", "/Search.aspx?tab=202", "/Search.aspx?tab=203")
        found: dict[str, Listing] = {}
        try:
            for path in search_pages:
                response = self.session.get(urljoin(base, path), timeout=30)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                for link in soup.select('a[href*="Detail.aspx"]'):
                    url = urljoin(base, link["href"])
                    card = link.find_parent(class_=re.compile(r"imov|property|result|box|item", re.I)) or link.parent
                    text = card.get_text(" ", strip=True)
                    if "lisboa" not in text.casefold():
                        continue
                    location = re.search(r"Concelho\s*:\s*([^|]+?)(?:\s+Freguesia|\s+Imóvel|$)", text, re.I)
                    municipality = location.group(1).strip() if location else "Lisboa"
                    if municipality.casefold() != "lisboa":
                        continue
                    title = link.get_text(" ", strip=True) or text[:160]
                    price = re.search(r"([\d.\s]+(?:,\d{1,2})?)\s*€", text)
                    image = card.select_one("img[src], img[data-src]") if card else None
                    found[url] = Listing("Millennium BCP Imoveis", title, text, municipality, published_price_eur=parse_euro_amount(price.group(1)) if price else None, url=url, last_seen=now(), image_url=(image.get("data-src") or image.get("src") or "") if image else "")
            results = list(found.values())
            self.record(source, len(results), "no Lisbon property records currently exposed" if not results else "")
            return results
        except requests.RequestException as error:
            self.record(source, error=str(error))
            return []

    def montepio_imoveis(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Montepio Imoveis")
        try:
            soup = BeautifulSoup(self.get(source.url).text, "html.parser")
            results: dict[str, Listing] = {}
            for link in soup.select('a[href*="/Comprar/"][href*="uid="]'):
                href = link.get("href", "")
                path_parts = [part for part in href.split("?")[0].split("/") if part]
                if len(path_parts) < 2 or path_parts[-2].casefold() != "lisboa":
                    continue
                card = link.find_parent(class_=re.compile(r"card|result|imovel|property|item", re.I)) or link.parent
                text = card.get_text(" ", strip=True)
                title = link.get_text(" ", strip=True) or text[:160]
                price = re.search(r"([\d.\s]+(?:,\d{1,2})?)\s*€", text)
                image = card.select_one("img[src], img[data-src]") if card else None
                url = urljoin(source.url, href)
                results[url] = Listing("Montepio Imoveis", title, text, "Lisboa", published_price_eur=parse_euro_amount(price.group(1)) if price else None, url=url, last_seen=now(), image_url=(image.get("data-src") or image.get("src") or "") if image else "")
            rows = list(results.values())
            self.record(source, len(rows), "no Lisbon property records currently exposed" if not rows else "")
            return rows
        except requests.RequestException as error:
            self.record(source, error=str(error))
            return []

    def imobancos(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Imobancos")
        api = "https://imobancos.pt/api/properties/fetchProperties"
        filters = (
            '(prop_type = "Apartamento" OR prop_type = "Moradia") AND '
            '(prop_purpose = "Comprar" OR prop_purpose = "Venda" OR prop_purpose = "comprar" OR prop_purpose = "venda") AND '
            'prop_district = "Lisboa"'
        )
        results: list[Listing] = []
        try:
            page = 1
            while True:
                response = self.session.post(api, json={"page": page, "hitsPerPage": 50, "filters": filters}, timeout=30)
                response.raise_for_status()
                data = response.json()
                hits = data.get("hits", [])
                if not hits:
                    break
                for hit in hits:
                    property_id = hit.get("id")
                    if not property_id:
                        continue
                    photos = hit.get("photos") or []
                    image = photos[0].get("photo_url", "") if photos else ""
                    parish = hit.get("prop_parish", "")
                    county = hit.get("prop_county", "")
                    district = hit.get("prop_district", "Lisboa")
                    results.append(Listing(
                        "Imobancos",
                        hit.get("prop_title") or hit.get("prop_name") or "Imovel",
                        ", ".join(filter(None, (parish, county, district))) or district,
                        county or district,
                        freguesia=parish,
                        published_price_eur=hit.get("prop_price"),
                        url=f"https://imobancos.pt/imoveis/{property_id}",
                        last_seen=now(),
                        image_url=image,
                    ))
                if page >= data.get("totalPages", page):
                    break
                page += 1
            self.record(source, len(results), "no Lisbon property records currently exposed" if not results else "")
            return results
        except requests.RequestException as error:
            self.record(source, error=str(error))
            return []

    def bankinter_imoveis(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Bankinter Imoveis")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36", locale="pt-PT")
                page.goto(source.url, wait_until="networkidle", timeout=90000)
                soup = BeautifulSoup(page.content(), "html.parser")
                browser.close()
            rows = []
            for link in soup.select('a[href]'):
                text = link.get_text(" ", strip=True)
                if "lisboa" not in text.casefold() or not any(term in link.get("href", "").casefold() for term in ("imovel", "property", "casa")):
                    continue
                rows.append(Listing("Bankinter Imoveis", text, text, "Lisboa", url=urljoin(source.url, link["href"]), last_seen=now()))
            self.record(source, len(rows), "no public Lisbon listing links currently exposed" if not rows else "")
            return rows
        except Exception as error:
            self.record(source, error=f"browser adapter failed: {error}")
            return []

    def portal_das_financas(self, profile_path: Path = TAX_BROWSER_PROFILE) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Portal das Financas")
        results: list[Listing] = []
        try:
            if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                raise RuntimeError("no graphical display; run from a desktop session or configure X11/Wayland forwarding")
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_path),
                    headless=False,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                    locale="pt-PT",
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(TAX_LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
                print("Complete the Portal das Financas login in the browser window.", file=sys.stderr)
                input("Press Enter here after login is complete: ")
                self.portal_das_financas_open_lisboa_listings(page, profile_path)
                results = self.portal_das_financas_collect_pages(page)
                self.portal_das_financas_enrich_locations(page, results)
                if not results:
                    debug_path = profile_path.parent / "portal-das-financas-debug.html"
                    debug_path.write_text(page.content(), encoding="utf-8")
                    print(f"No Lisbon rows matched; saved the rendered page to {debug_path} for inspection.", file=sys.stderr)
                context.close()
            self.record(source, len(results), "authenticated session returned no Lisbon property records" if not results else "")
        except Exception as error:
            self.record(source, error=f"authenticated browser adapter failed: {error}")
        return results

    def portal_das_financas_open_lisboa_listings(self, page, profile_path: Path) -> None:
        """Navigate from the post-login landing page to the Imoveis list filtered by Lisboa, when the site exposes those controls."""
        page.goto(TAX_SALES_URL, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(1500)
        checkbox_dump = profile_path.parent / "portal-das-financas-checkboxes.json"
        checkbox_dump.write_text(json.dumps(self.portal_das_financas_checkbox_info(page), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved {checkbox_dump.name} with every checkbox's id/name/label for diagnosis.", file=sys.stderr)
        if not self.portal_das_financas_isolate_checkbox(page, r"^im[oó]veis$"):
            print("warning: could not isolate 'Imoveis' in the Categorias filter", file=sys.stderr)
        if not self.portal_das_financas_isolate_checkbox(page, r"^lisboa$"):
            print("warning: could not isolate 'Lisboa' in the Distrito filter", file=sys.stderr)
        apply_button = page.get_by_role("button", name=re.compile(r"aplicar filtros|aplicar|pesquisar|procurar", re.I)).first
        if apply_button.count():
            try:
                apply_button.click(timeout=10000)
                page.wait_for_load_state("networkidle", timeout=60000)
            except Exception as error:
                print(f"warning: could not click Aplicar filtros: {error}", file=sys.stderr)
        else:
            print("warning: 'Aplicar filtros' button not found", file=sys.stderr)
        page.wait_for_timeout(1500)

    CHECKBOX_INFO_SCRIPT = """els => els.map((el, i) => {
        const id = el.id || '';
        const name = el.getAttribute('name') || '';
        const idCandidates = [id, id.replace(/1$/, '')];
        let labelFor = null;
        for (const candidate of idCandidates) {
            if (!candidate) continue;
            labelFor = document.querySelector(`label[for="${CSS.escape(candidate)}"]`);
            if (labelFor) break;
        }
        const closestLabel = el.closest('label');
        const checkboxRow = el.closest('.checkbox-vendas');
        const nameLabel = checkboxRow ? checkboxRow.querySelector('label.font-thin') : null;
        const genericRow = el.closest('tr, li');
        const aria = el.getAttribute('aria-label') || '';
        const next = el.nextElementSibling ? el.nextElementSibling.innerText : '';
        const label = (labelFor && labelFor.innerText) || (closestLabel && closestLabel.innerText) || (nameLabel && nameLabel.innerText) || aria || next || (genericRow && genericRow.innerText) || '';
        const group = checkboxRow ? (checkboxRow.id || checkboxRow.className) : '';
        return {index: i, id, name, checked: el.checked, label: label.trim().slice(0, 80), group};
    })"""

    def portal_das_financas_checkbox_info(self, page) -> list[dict]:
        return page.locator("input[type=checkbox]").evaluate_all(self.CHECKBOX_INFO_SCRIPT)

    @staticmethod
    def _checkbox_group_key(value: str) -> str:
        """Collapse the numeric index out of ids/names like lstCategoriasAll3.selecionado1 so siblings share a key."""
        return re.sub(r"\d+", "", value or "")

    def portal_das_financas_isolate_checkbox(self, page, option_regex: str) -> bool:
        """Deselect every other checkbox in the option's filter group, then check only the matching option."""
        pattern = re.compile(option_regex, re.I)
        checkboxes = page.locator("input[type=checkbox]")
        if not checkboxes.count():
            return False
        infos = self.portal_das_financas_checkbox_info(page)
        target = next((info for info in infos if pattern.search(info["label"])), None)
        if target is None:
            return False
        target_locator = checkboxes.nth(target["index"])
        if target.get("group"):
            group = [info for info in infos if info.get("group") == target["group"]]
        else:
            key = self._checkbox_group_key(target["id"] or target["name"])
            group = [info for info in infos if key and self._checkbox_group_key(info["id"] or info["name"]) == key]
        if len(group) <= 1:
            container = target_locator.locator("xpath=ancestor::fieldset[1]")
            if not container.count():
                container = target_locator.locator("xpath=ancestor::*[self::ul or self::div][.//input[@type='checkbox']][1]")
            group_locators = [container.locator("input[type=checkbox]").nth(i) for i in range(container.locator("input[type=checkbox]").count())] if container.count() else []
        else:
            group_locators = [checkboxes.nth(info["index"]) for info in group]
        for box in group_locators:
            try:
                if box.is_checked():
                    box.uncheck(timeout=5000, force=True)
            except Exception:
                continue
        try:
            target_locator.check(timeout=5000, force=True)
        except Exception:
            target_locator.click(timeout=5000)
        return True

    def portal_das_financas_collect_pages(self, page, max_pages: int = 20) -> list[Listing]:
        results: list[Listing] = []
        seen_urls: set[str] = set()
        for _ in range(max_pages):
            for listing in self.portal_das_financas_parse(page):
                if listing.url in seen_urls:
                    continue
                seen_urls.add(listing.url)
                results.append(listing)
            next_page = page.get_by_role("link", name=re.compile(r"seguinte|pr[oó]xim", re.I)).first
            if not next_page.count() or next_page.is_disabled():
                break
            try:
                next_page.click(timeout=10000)
                page.wait_for_load_state("networkidle", timeout=60000)
                page.wait_for_timeout(1000)
            except Exception:
                break
        return results

    def portal_das_financas_enrich_locations(self, page, listings: list[Listing]) -> None:
        for listing in listings:
            if not listing.url or "venda=" not in listing.url:
                continue
            detail_page = page.context.new_page()
            try:
                detail_page.goto(listing.url, wait_until="domcontentloaded", timeout=30000)
                detail_text = detail_page.locator("body").inner_text(timeout=10000)
                freguesia = self.portal_das_financas_extract_freguesia(detail_text)
                if freguesia:
                    listing.freguesia = freguesia
                    listing.address = f"Freguesia: {freguesia}"
            except Exception as error:
                print(f"warning: could not read freguesia for {listing.title}: {error}", file=sys.stderr)
            finally:
                detail_page.close()

    @staticmethod
    def portal_das_financas_extract_freguesia(text: str) -> str:
        patterns = (
            r"\bFREGUESIA\s*:?\s*(?:(?:\d+)\s*[–-]\s*)?(?:de\s+)?([^,;\n)]+)",
            r"\bfreguesia\s+(?:de\s+)?([^,;\n)]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).strip(" :|-–\t")
        return ""

    def portal_das_financas_parse(self, page) -> list[Listing]:
        """Parse the #tabelaBens result cards; the district/category filters already restrict this to Lisboa Imoveis."""
        soup = BeautifulSoup(page.content(), "html.parser")
        results: list[Listing] = []
        for card in soup.select("#tabelaBens .card.card-list"):
            spans = card.select(".card-title span")
            title = spans[0].get_text(strip=True) if spans else "Imovel"
            reference = spans[1].get_text(strip=True) if len(spans) > 1 else ""
            sale_type = card.select_one(".label")
            sale_type_text = sale_type.get_text(strip=True) if sale_type else ""
            text = " | ".join(card.stripped_strings)
            values = [parse_euro_amount(el.get_text()) for el in card.select(".row.margin-top-sm + .row strong")]
            base_value = values[0] if len(values) > 0 else None
            current_value = values[1] if len(values) > 1 else None
            closing = re.search(r"Encerra a\s*([\d-]+)\s*às\s*([\d:]+)h", text, re.I)
            auction_date = f"{closing.group(1)} {closing.group(2)}h" if closing else ""
            image = card.select_one("img.img-fluid")
            detail_button = card.select_one("#btnDetalhe")
            venda_id = detail_button.get("value", "") if detail_button else ""
            url = urljoin(page.url, f"/vendasat/detalhe?venda={venda_id}") if venda_id else page.url
            is_auction = "leil" in sale_type_text.casefold()
            results.append(Listing(
                "Portal das Financas",
                f"{title} {reference}".strip(),
                "Lisboa",
                "Lisboa",
                current_bid_eur=current_value if is_auction else None,
                minimum_bid_eur=base_value if is_auction else None,
                published_price_eur=None if is_auction else base_value,
                auction_date=auction_date,
                url=url,
                last_seen=now(),
                image_url=image.get("src", "") if image else "",
            ))
        return results

    def caixa_imobiliario(self) -> list[Listing]:
        source = next(item for item in SOURCES if item.name == "Caixa Imobiliario")
        results: list[Listing] = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(locale="pt-PT")
                page.goto(source.url, wait_until="networkidle", timeout=60000)
                accept_cookies = page.get_by_role("button", name="Aceitar")
                if accept_cookies.count():
                    accept_cookies.click()
                seen_urls: set[str] = set()
                while True:
                    soup = BeautifulSoup(page.content(), "html.parser")
                    for listing in self.caixa_parse_results(soup, page.url):
                        if listing.url not in seen_urls:
                            seen_urls.add(listing.url)
                            results.append(listing)
                    next_button = page.get_by_role("button", name="Próxima página")
                    if not next_button.count() or next_button.is_disabled():
                        break
                    first_url = page.locator('.property-card a[aria-label="Saber mais"]').first.get_attribute("href")
                    next_button.click()
                    page.wait_for_function("old => document.querySelector('.property-card a[aria-label=\\\"Saber mais\\\"]')?.getAttribute('href') !== old", arg=first_url, timeout=60000)
                    page.wait_for_selector('.property-card a[aria-label="Saber mais"]', timeout=60000)
                browser.close()
            self.record(source, len(results))
        except Exception as error:
            self.record(source, error=f"browser adapter failed: {error}")
        return results

    def caixa_parse_results(self, soup: BeautifulSoup, page_url: str) -> list[Listing]:
        listings: list[Listing] = []
        for card in soup.select(".property-card"):
            text = card.get_text(" ", strip=True)
            location_node = card.select_one(".property-card__location")
            location = location_node.get_text(" ", strip=True) if location_node else ""
            location_parts = [part.strip() for part in location.split(",") if part.strip()]
            municipality = location_parts[-2] if len(location_parts) >= 2 else ""
            if municipality.casefold() != "lisboa":
                continue
            link = card.select_one('a[aria-label="Saber mais"][href]')
            if not link:
                continue
            title = next((heading.get_text(" ", strip=True) for heading in card.select("h2, h3, h4")), text[:160])
            price = re.search(r"([\d.\s]+(?:,\d{1,2})?)\s*€", text)
            image = card.select_one("img[src], img[data-src]")
            listings.append(Listing("Caixa Imobiliario", title, location, municipality, published_price_eur=parse_euro_amount(price.group(1)) if price else None, url=urljoin(page_url, link["href"]), last_seen=now(), image_url=(image.get("data-src") or image.get("src") or "") if image else ""))
        return listings

    def unavailable_sources(self) -> None:
        implemented = {"Leilosoc", "Euro Estates", "e-leiloes", "Leiloatrium", "OneFix", "Citius", "Santander Imoveis", "Seguranca Social", "Caixa Imobiliario", "Millennium BCP Imoveis", "Bankinter Imoveis", "Montepio Imoveis"}
        for source in SOURCES:
            if source.name not in implemented:
                try:
                    response = self.get(source.url)
                    self.record(source, error=f"no adapter implemented; endpoint returned HTTP {response.status_code}")
                except requests.RequestException as error:
                    self.record(source, error=str(error))

    def citius(self) -> list[Listing]:
        """Query every court in the public judicial-sales search form; the site requires one court per search and has no date filter applied."""
        source = next(item for item in SOURCES if item.name == "Citius")
        search_url = source.url
        results: list[Listing] = []
        courts_checked = 0
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                # A generic Playwright UA is blocked by the portal's bot filter; a desktop Chrome UA is required.
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="pt-PT",
                )
                page = context.new_page()
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_selector("#ctl00_ContentPlaceHolder1_ddlTribunais", timeout=30000)
                courts = [value for value in page.locator("#ctl00_ContentPlaceHolder1_ddlTribunais option").evaluate_all("options => options.map(o => o.value)") if value and value != "0"]
                for value in courts:
                    courts_checked += 1
                    try:
                        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_selector("#ctl00_ContentPlaceHolder1_ddlTribunais", timeout=30000)
                        page.select_option("#ctl00_ContentPlaceHolder1_ddlTribunais", value)
                        page.select_option("#ctl00_ContentPlaceHolder1_ddlTiposBem", "1")  # Imovel only
                        page.click("#ctl00_ContentPlaceHolder1_btnSearch")
                        page.wait_for_load_state("networkidle", timeout=60000)
                        soup = BeautifulSoup(page.content(), "html.parser")
                        results.extend(self.citius_parse_results(soup, search_url))
                    except Exception as error:
                        print(f"warning: Citius court {value}: {error}", file=sys.stderr)
                browser.close()
            self.record(source, len(results), f"checked {courts_checked} courts nationwide (dates ignored); kept only Lisboa property matches")
        except Exception as error:
            self.record(source, error=f"browser adapter failed: {error}")
        return results

    def citius_parse_results(self, soup: BeautifulSoup, search_url: str) -> list[Listing]:
        panel = soup.select_one("#divresultadopubvenda") or soup.select_one("#ctl00_ContentPlaceHolder1_pnlResults")
        if not panel:
            return []
        listings: list[Listing] = []
        for table in panel.select("table"):
            headers = [cell.get_text(" ", strip=True).lower() for cell in table.select("thead th")]
            if not headers:
                headers = [cell.get_text(" ", strip=True).lower() for cell in table.select("tr")[0].select("th")] if table.select("tr") else []
            location_index = next((index for index, header in enumerate(headers) if re.search(r"localiza|morada|concelho|freguesia|distrito|local do bem", header)), None)
            if location_index is None:
                continue
            for row in table.select("tr"):
                cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
                if not cells or location_index >= len(cells) or "lisboa" not in cells[location_index].lower():
                    continue
                row_text = " | ".join(cells)
                link = row.select_one("a[href]")
                price = re.search(r"([\d.\s]+(?:,\d{1,2})?\s*€)", row_text)
                listings.append(Listing(
                    "Citius",
                    cells[0][:200] if cells[0] else row_text[:120],
                    cells[location_index],
                    "Lisboa",
                    published_price_eur=parse_euro_amount(price.group(1)) if price else None,
                    url=urljoin(search_url, link["href"]) if link and link.get("href") else search_url,
                    last_seen=now(),
                ))
        return listings

    def browser_probe(self) -> None:
        """Recheck blocked sources with JavaScript and a browser session."""
        targets = {
            "e-leiloes": "https://www.e-leiloes.pt/index.aspx",
            "Portal das Financas": "https://vendas.portaldasfinancas.gov.pt/",
        }
        known = {item["source"] for item in self.status}
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                for name, url in targets.items():
                    if name not in known:
                        continue
                    entry = next(item for item in self.status if item["source"] == name)
                    if entry["listings"]:
                        continue
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        text = page.locator("body").inner_text(timeout=10000).strip()
                        status = response.status if response else 0
                        entry["error"] = f"browser HTTP {status}; no usable public listing content" if not text or status >= 400 else f"browser HTTP {status}; page requires a source-specific adapter"
                    except Exception as error:
                        entry["error"] = f"browser probe failed: {error}"
                browser.close()
        except Exception as error:
            print(f"warning: browser probes unavailable: {error}", file=sys.stderr)

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_euro_amount(value: str) -> float | None:
    match = re.search(r"([\d.\s]+(?:,\d{1,2})?)", value)
    if not match:
        return None
    try:
        return float(match.group(1).replace(" ", "").replace("\xa0", "").replace(".", "").replace(",", "."))
    except ValueError:
        return None

def deduplicate(listings: Iterable[Listing]) -> list[Listing]:
    found: dict[tuple[str, str], Listing] = {}
    for listing in listings:
        found.setdefault((listing.address.lower(), listing.title.lower()), listing)
    return list(found.values())

def write_output(listings: Iterable[Listing], statuses: Iterable[dict[str, str | int]], db_path: str, fmt: str, csv_output: str = "") -> None:
    listing_rows = [asdict(item) for item in listings]
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        for source in SOURCES:
            db.upsert_source(conn, source.name, source.url, source.category, source.notes, listing_type="auction")
        conn.execute("DELETE FROM listings WHERE source = ?", ("Caixa Imobiliario",))
        for item in listing_rows:
            db.upsert_listing(conn, item, listing_type="auction")
        for status in statuses:
            db.upsert_source_status(conn, status["source"], "auction", status.get("listings", 0), status.get("error", ""))
    if fmt == "csv":
        with Path(csv_output or "lisbon-auctions.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=Listing.__dataclass_fields__)
            writer.writeheader()
            writer.writerows(listing_rows)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--crawl", action="store_true", help="crawl all configured sources")
    parser.add_argument("--tax", action="store_true", help="open a dedicated browser profile for interactive Portal das Financas login and crawl it")
    parser.add_argument("--tax-profile", type=Path, default=TAX_BROWSER_PROFILE, help="directory for the persistent Portal das Financas browser profile")
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="path to the SQLite database")
    parser.add_argument("--format", choices=("json", "csv"), default="json", help="also write a CSV export alongside the database")
    parser.add_argument("--csv-output", default="lisbon-auctions.csv")
    args = parser.parse_args()
    if args.inventory:
        print(json.dumps([asdict(source) for source in SOURCES], ensure_ascii=False, indent=2))
        return 0
    if args.crawl:
        crawler = Crawler()
        listings = deduplicate(crawler.leilosoc() + crawler.euro_estates() + crawler.e_leiloes() + crawler.leiloatrium() + crawler.onefix() + crawler.citius() + crawler.santander_imoveis() + crawler.seguranca_social() + crawler.caixa_imobiliario() + crawler.millennium_imoveis() + crawler.bankinter_imoveis() + crawler.montepio_imoveis() + crawler.imobancos())
        crawler.unavailable_sources()
        crawler.browser_probe()
        write_output(listings, crawler.status, args.db, args.format, args.csv_output)
        print(f"Wrote {len(listings)} Lisbon listings and {len(crawler.status)} source statuses to {args.db}")
        return 0
    if args.tax:
        crawler = Crawler()
        listings = deduplicate(crawler.portal_das_financas(args.tax_profile))
        error = next((status["error"] for status in crawler.status if status["source"] == "Portal das Financas" and status["error"]), "")
        if not error:
            db.init_db(args.db)
            with db.connect(args.db) as conn:
                conn.execute("DELETE FROM listings WHERE source = ? AND listing_type = ?", ("Portal das Financas", "auction"))
        write_output(listings, crawler.status, args.db, args.format, args.csv_output)
        if error:
            print(f"Portal das Financas crawl failed: {error}", file=sys.stderr)
            return 1
        print(f"Wrote {len(listings)} Portal das Financas Lisbon listings to {args.db}")
        return 0
    parser.error("choose --inventory or --crawl")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
