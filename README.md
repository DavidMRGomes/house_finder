# House Finder

This project crawls public Portuguese property-auction sources for the Lisbon
district. Results are stored in a local SQLite database (`house_finder.db`)
that categorizes every listing, source, and discovery link.

## Usage

```sh
python3 scripts/auction_finder.py --inventory
python3 scripts/auction_finder.py --crawl --db house_finder.db
python3 scripts/auction_finder.py --tax --db house_finder.db
python3 scripts/market_crawler.py --db house_finder.db
python3 scripts/build_report.py --db house_finder.db --output houses.html
```

The `--tax` command opens a dedicated visible browser profile for Portal das
Finanças. Log in directly in that browser, including any MFA step, then press
Enter in the terminal. Credentials are never read or stored by the crawler;
the local session profile is kept in `.portal-das-financas-browser/` and is
ignored by git.

The crawler uses Playwright for browser-level diagnostics. Set it up once with:

```sh
python3 -m pip install playwright requests beautifulsoup4
python3 -m playwright install chromium
```

The crawler identifies itself, keeps a session for form-based sources, and
continues when a source is unavailable. It does not bypass CAPTCHAs, login
walls, robots restrictions, TLS validation, or other access controls.

Prices are represented separately as `current_bid_eur`, `minimum_bid_eur`, and
`published_price_eur`. An extractor must not infer an auction bid from a base
or ordinary sale price. Missing values remain `null`.

authenticated area.
Currently implemented listing adapters are Leilosoc, Euro Estates, e-Leilões,
OneFix, Caixa Imobiliário, Millennium BCP, Montepio, Bankinter, Segurança
Social, and Santander Imóveis. Leiloatrium is checked through Chromium and
currently reports zero Lisbon lots. The e-Leilões and OneFix adapters extract
public bids, minimum bids, base values, and auction dates. Citius checks every
court and keeps only properties whose location column is in Lisboa. Some
sources may return zero records or an access-status message when no public
Lisbon inventory is currently exposed.

## Database

`scripts/db.py` defines the SQLite schema and read/write helpers used by every script:

- `listings` — one row per auction or market listing, keyed by URL, tagged
  with `listing_type` (`auction` or `market`), category fields (source,
  municipality, address), all price fields, and a cached listing image.
- `sources` — static metadata about each configured source (URL, category,
  notes).
- `source_status` — the latest crawl result per source (listing count, error).
- `discovery_links` — auxiliary links discovered while crawling a source.

Re-running a crawler upserts rows by URL/name, so the database always reflects
the latest crawl without growing unbounded.

Open `houses.html` in a browser for the visual report. It includes
search, source filtering, municipality filtering, price range filters, a
"bid published" filter, price sorting, listing links, source-page images,
and a source coverage ledger. Re-run `scripts/build_report.py` after crawling to
refresh it from the database.

The **Market homes** tab is populated by `market_crawler.py`. Its first public
adapter currently extracts Lisbon-region listings from CustoJusto; other
portals remain linked with their access status until they expose verifiable
listing data.