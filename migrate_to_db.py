#!/usr/bin/env python3
"""One-time import of the legacy JSON snapshots into the SQLite database."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import db


def migrate(source_paths: list[Path], db_path: str) -> None:
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        for source_path in source_paths:
            data = json.loads(source_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "listings" in data:
                for listing in data.get("listings", []):
                    db.upsert_listing(conn, listing, listing_type="auction")
                for status in data.get("sources", []):
                    db.upsert_source_status(conn, status["source"], "auction", status.get("listings", 0), status.get("error", ""))
            elif isinstance(data, dict) and "market_listings" in data:
                for listing in data.get("market_listings", []):
                    db.upsert_listing(conn, listing, listing_type="market")
                for status in data.get("market_status", []):
                    db.upsert_source_status(conn, status["source"], "market", status.get("listings", 0), status.get("error", ""))
            elif isinstance(data, list):
                for item in data:
                    if "url" not in item:
                        continue
                    if "status" in item and "name" in item:
                        db.upsert_source(conn, item["name"], item.get("url", ""), "market", item.get("notes", ""), listing_type="market")
                        db.upsert_source_status(conn, item["name"], "market", 0, item.get("status", ""))
                    elif "title" in item:
                        db.upsert_listing(conn, item, listing_type="auction")
                    else:
                        db.upsert_discovery_link(conn, item.get("source", ""), item.get("url", ""), item.get("text", ""))
    print(f"Migrated {len(source_paths)} JSON files into {db_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="*", type=Path, help="JSON files to import; defaults to every JSON file in the workspace")
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    args = parser.parse_args()
    inputs = args.input or sorted(Path.cwd().glob("*.json"))
    migrate(inputs, args.db)
