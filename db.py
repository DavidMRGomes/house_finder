#!/usr/bin/env python3
"""SQLite storage layer for house-finder listings, sources, and discovery links."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "house_finder.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    url TEXT,
    category TEXT,
    notes TEXT,
    listing_type TEXT NOT NULL DEFAULT 'auction'
);

CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    listing_type TEXT NOT NULL DEFAULT 'auction',
    listings_count INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    checked_at TEXT
);

CREATE TABLE IF NOT EXISTS listings (
    url TEXT PRIMARY KEY,
    listing_type TEXT NOT NULL, -- 'auction' or 'market'
    source TEXT NOT NULL,
    title TEXT,
    address TEXT,
    municipality TEXT,
    current_bid_eur REAL,
    minimum_bid_eur REAL,
    published_price_eur REAL,
    auction_date TEXT,
    image_url TEXT,
    image TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS discovery_links (
    url TEXT PRIMARY KEY,
    source TEXT,
    text TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_type ON listings(listing_type);
CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source);
CREATE INDEX IF NOT EXISTS idx_listings_municipality ON listings(municipality);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: Path | str = DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_source(conn: sqlite3.Connection, name: str, url: str, category: str, notes: str, listing_type: str = "auction") -> None:
    conn.execute(
        "INSERT INTO sources (name, url, category, notes, listing_type) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET url=excluded.url, category=excluded.category, notes=excluded.notes, listing_type=excluded.listing_type",
        (name, url, category, notes, listing_type),
    )


def upsert_source_status(conn: sqlite3.Connection, source: str, listing_type: str, listings_count: int = 0, error: str = "") -> None:
    conn.execute(
        "INSERT INTO source_status (source, listing_type, listings_count, error, checked_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(source) DO UPDATE SET listing_type=excluded.listing_type, listings_count=excluded.listings_count, "
        "error=excluded.error, checked_at=excluded.checked_at",
        (source, listing_type, listings_count, error, now()),
    )


def upsert_listing(conn: sqlite3.Connection, listing: dict, listing_type: str) -> None:
    if not listing.get("url"):
        return
    conn.execute(
        "INSERT INTO listings (url, listing_type, source, title, address, municipality, current_bid_eur, "
        "minimum_bid_eur, published_price_eur, auction_date, image_url, last_seen) "
        "VALUES (:url, :listing_type, :source, :title, :address, :municipality, :current_bid_eur, "
        ":minimum_bid_eur, :published_price_eur, :auction_date, :image_url, :last_seen) "
        "ON CONFLICT(url) DO UPDATE SET listing_type=excluded.listing_type, source=excluded.source, "
        "title=excluded.title, address=excluded.address, municipality=excluded.municipality, "
        "current_bid_eur=excluded.current_bid_eur, minimum_bid_eur=excluded.minimum_bid_eur, "
        "published_price_eur=excluded.published_price_eur, auction_date=excluded.auction_date, "
        "image_url=excluded.image_url, last_seen=excluded.last_seen",
        {
            "url": listing.get("url", ""),
            "listing_type": listing_type,
            "source": listing.get("source", ""),
            "title": listing.get("title", ""),
            "address": listing.get("address", ""),
            "municipality": listing.get("municipality", ""),
            "current_bid_eur": listing.get("current_bid_eur"),
            "minimum_bid_eur": listing.get("minimum_bid_eur"),
            "published_price_eur": listing.get("published_price_eur"),
            "auction_date": listing.get("auction_date", ""),
            "image_url": listing.get("image_url", ""),
            "last_seen": listing.get("last_seen", now()),
        },
    )


def upsert_discovery_link(conn: sqlite3.Connection, source: str, url: str, text: str) -> None:
    if not url:
        return
    conn.execute(
        "INSERT INTO discovery_links (url, source, text) VALUES (?, ?, ?) "
        "ON CONFLICT(url) DO UPDATE SET source=excluded.source, text=excluded.text",
        (url, source, text),
    )


def set_listing_image(conn: sqlite3.Connection, url: str, image: str) -> None:
    conn.execute("UPDATE listings SET image = ? WHERE url = ?", (image, url))


def fetch_listings(conn: sqlite3.Connection, listing_type: str | None = None) -> list[dict]:
    if listing_type:
        rows = conn.execute("SELECT * FROM listings WHERE listing_type = ? ORDER BY source, title", (listing_type,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM listings ORDER BY source, title").fetchall()
    return [dict(row) for row in rows]


def fetch_sources(conn: sqlite3.Connection, listing_type: str | None = None) -> list[dict]:
    query = "SELECT source_status.source AS name, COALESCE(sources.url, '') AS url, " \
            "COALESCE(sources.category, '') AS category, COALESCE(sources.notes, '') AS notes, " \
            "source_status.listing_type AS listing_type, source_status.listings_count, " \
            "source_status.error, source_status.checked_at FROM source_status " \
            "LEFT JOIN sources ON sources.name = source_status.source"
    params: tuple = ()
    if listing_type:
        query += " WHERE source_status.listing_type = ?"
        params = (listing_type,)
    query += " ORDER BY source_status.source"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def fetch_discovery_links(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM discovery_links ORDER BY source, url").fetchall()]
