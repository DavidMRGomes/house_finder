#!/usr/bin/env bash
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR"

python3 scripts/auction_finder.py --crawl --db house_finder.db
python3 scripts/auction_finder.py --tax --db house_finder.db
python3 scripts/market_crawler.py --db house_finder.db
python3 scripts/build_report.py --db house_finder.db --output houses.html