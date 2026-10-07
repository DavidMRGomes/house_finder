# House Finder

This project crawls public Portuguese property-auction sources for the Lisbon
district. Results are stored in a local SQLite database (`house_finder.db`)
that categorizes every listing, source, and discovery link.

## Usage

```sh
./update_all.sh
python3 scripts/auction_finder.py --inventory
python3 scripts/auction_finder.py --crawl --db house_finder.db
python3 scripts/auction_finder.py --tax --db house_finder.db
python3 scripts/market_crawler.py --db house_finder.db
python3 scripts/build_report.py --db house_finder.db --output index.html
```

Run `./update_all.sh` to crawl the configured auction and market sources in
sequence, including the interactive Portal das Finanças login, then rebuild
`index.html`. Source-level blocks and failures are recorded in the report.
The script pauses after the public auction crawl while you complete the tax
portal login in the browser window.

Market homes are ordered newest first when a source publishes `datePosted` or
`datePublished`; sources without a publication date fall back to crawl time.
The Market homes tab supports maximum price, Concelho, and Freguesia filters.
It also supports multi-select Typology filters such as T0, T1, and T2. Location
filters only expose values explicitly published by each source; a portal that
returns only `Lisboa` cannot be safely assigned a Freguesia.
Market snapshots retain a rolling 90-day window. Each successful source crawl
marks unseen previous listings as no longer listed, while new listings are
recorded in `listing_events`; failed or blocked sources do not deactivate old
records.
Each configured market portal has a named adapter. The report footer separates
sources crawled successfully from sources that were blocked, empty, or failed.

The `--tax` command opens a dedicated visible browser profile for Portal das
Finanças. Log in directly in that browser, including any MFA step, then press
Enter in the terminal. The crawler opens the SIVI sales list after login and
scrapes that page. Credentials are never read or stored by the crawler;
the local session profile is kept in `.portal-das-financas-browser/` and is
ignored by git. This interactive command requires a graphical desktop; a
headless SSH or VS Code server session needs X11/Wayland forwarding to display
the login window.

A GitHub Actions workflow (`.github/workflows/nightly-crawl.yml`) runs nightly and on demand. It crawls the public auction sources (`auction_finder.py --crawl`) and the market sources, rebuilds `index.html`, and commits it with `house_finder.db`. The auction step cannot fail the run. `--tax` is not part of it, because it needs an interactive Portal das Finanças login; run it manually when you want to refresh that source. Existing Portal das Finanças listings are kept between nightly runs.

The crawler uses Playwright for browser-level diagnostics. Set it up once with:

```sh
python3 -m pip install -r requirements.txt
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

Open `index.html` in a browser for the visual report. It includes
search, source filtering, municipality filtering, price range filters, a
"bid published" filter, price sorting, listing links, source-page images,
and a source coverage ledger. Re-run `scripts/build_report.py` after crawling to
refresh it from the database.

The **Market homes** tab is populated by `market_crawler.py`. It checks every configured market source with a named adapter. It extracts verifiable public records from CustoJusto and Century 21, attempts source-specific routes for Casayes (Lisboa concelho, via its search API), JLL Residential (Lisboa concelho, via its Ego Real Estate API), Keller Williams Portugal (Lisboa concelho apartments and houses, via its search API), Engel & Völkers (Lisboa apartments and houses, read from its search pages), Savills (Lisboa, read from its list pages), Porta da Frente (Lisboa; the cheapest priced unit per development plus standalone listings, read from its search pages), SAPO Imóveis (Lisboa apartments from its search pages, crawled slowly to avoid rate limits), RE/MAX, iad, Zome, Pure Portugal, HomeLovers, Imovirtual, and the other configured portals, and records a status for every source. Use `--source NAME` (repeatable, case-insensitive substring) to crawl only some sources, e.g. `python3 scripts/market_crawler.py --db house_finder.db --source savills`. Market records are ordered by publication date when provided by the source, otherwise by crawl time.

Listings can be saved as favorites from their cards and shown alone with the **Favorites only** checkbox, which also shows how many favorites exist. Favorites are stored in the repository's `favorites.json`, so every device sees the same ones; saving or removing a favorite requires Owner login (log in on each device with your token). Favorites previously saved in a browser are uploaded the first time you log in as owner there. The **Hidden only** filter is available to all viewers; changing the shared blacklist with Hide or Restore still requires Owner login.

Each market listing has an **Add Note** button that opens a notes dialog where notes can be added, edited, and deleted. Once a listing has notes, they are shown directly on its card, and a **Notes (n)** button appears to manage them. Notes are stored in the repository's `notes.json`, so every device sees the same ones; adding, editing, or deleting a note requires Owner login. Notes previously saved in a browser are uploaded the first time you log in as owner there.