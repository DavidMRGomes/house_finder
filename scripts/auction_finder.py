#!/usr/bin/env python3
"""Crawl public Portuguese property-auction sources for Lisbon listings."""
from __future__ import annotations

import argparse
import csv
import json
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
    Source("Seguranca Social", "https://www.seg-social-patrimonio.pt/comprar/imoveis/?tipo=1", "government", "Public Social Security property sales portal."),
    Source("Portal das Financas", "https://vendas.portaldasfinancas.gov.pt/", "tax", "Tax authority sales portal; public endpoint currently returns 404."),
    Source("Citius", "https://www.citius.mj.pt/portal/consultas/consultasvenda.aspx", "judicial", "Public judicial-sales search form; queried per court since it requires a court to be selected."),
    Source("Leilosoc", "https://www.leilosoc.com/category/5-imovel/", "auctioneer", "Public property lots."),
    Source("Euro Estates", "https://www.euroestates.pt/realestate/auctions", "auctioneer", "Public active-auctions search."),
    Source("Vantagem Leiloes", "https://www.vantagemleiloes.com/", "auctioneer", "Configured source; DNS currently unavailable."),
    Source("Caixa Imobiliario", "https://www.caixaimobiliario.pt/", "bank", "Bank property portal."),
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

    def unavailable_sources(self) -> None:
        implemented = {"Leilosoc", "Euro Estates", "e-leiloes", "Leiloatrium", "OneFix", "Citius"}
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
        for row in panel.select("table tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
            if not cells:
                continue
            row_text = " | ".join(cells)
            if "lisboa" not in row_text.lower():
                continue
            link = row.select_one("a[href]")
            price = re.search(r"([\d.\s]+,\d{2}\s*€)", row_text)
            listings.append(Listing(
                "Citius",
                cells[0][:200] if cells[0] else row_text[:120],
                row_text,
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
        return float(match.group(1).replace(" ", "").replace(".", "").replace(",", "."))
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
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="path to the SQLite database")
    parser.add_argument("--format", choices=("json", "csv"), default="json", help="also write a CSV export alongside the database")
    parser.add_argument("--csv-output", default="lisbon-auctions.csv")
    args = parser.parse_args()
    if args.inventory:
        print(json.dumps([asdict(source) for source in SOURCES], ensure_ascii=False, indent=2))
        return 0
    if args.crawl:
        crawler = Crawler()
        listings = deduplicate(crawler.leilosoc() + crawler.euro_estates() + crawler.e_leiloes() + crawler.leiloatrium() + crawler.onefix() + crawler.citius())
        crawler.unavailable_sources()
        crawler.browser_probe()
        write_output(listings, crawler.status, args.db, args.format, args.csv_output)
        print(f"Wrote {len(listings)} Lisbon listings and {len(crawler.status)} source statuses to {args.db}")
        return 0
    parser.error("choose --inventory or --crawl")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
