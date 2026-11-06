import os
import sqlite3
from pathlib import Path

import pandas as pd

# Absolute path so the app and Airflow hit the same file no matter where they run from.
DB_FILE = os.environ.get(
    "PORTFOLIO_DB",
    str(Path(__file__).resolve().parent.parent / "portfolio.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_name TEXT NOT NULL,
    symbol TEXT NOT NULL UNIQUE,
    total_quantity REAL NOT NULL,
    account_quantities TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    symbol TEXT,
    stock_name TEXT,
    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
    trade_date TEXT NOT NULL,
    quantity REAL NOT NULL CHECK(quantity > 0),
    price REAL NOT NULL CHECK(price >= 0),
    fees REAL NOT NULL DEFAULT 0 CHECK(fees >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buy_txn_id INTEGER NOT NULL,
    person TEXT NOT NULL,
    symbol TEXT NOT NULL,
    buy_date TEXT NOT NULL,
    buy_price REAL NOT NULL,
    remaining_qty REAL NOT NULL,
    FOREIGN KEY(buy_txn_id) REFERENCES transactions(id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sell_txn_id INTEGER NOT NULL,
    buy_lot_id INTEGER NOT NULL,
    person TEXT NOT NULL,
    symbol TEXT NOT NULL,
    buy_date TEXT NOT NULL,
    sell_date TEXT NOT NULL,
    matched_qty REAL NOT NULL,
    buy_price REAL NOT NULL,
    sell_price REAL NOT NULL,
    realized_pl REAL NOT NULL,
    term TEXT NOT NULL CHECK(term IN ('SHORT','LONG')),
    FOREIGN KEY(sell_txn_id) REFERENCES transactions(id),
    FOREIGN KEY(buy_lot_id) REFERENCES lots(id)
);

CREATE TABLE IF NOT EXISTS daily_prices (
    symbol TEXT NOT NULL,
    price_date TEXT NOT NULL,
    close REAL NOT NULL CHECK(close > 0),
    loaded_at TEXT NOT NULL,
    PRIMARY KEY (symbol, price_date)
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    close REAL NOT NULL,
    market_value REAL NOT NULL,
    PRIMARY KEY (snapshot_date, symbol)
);

CREATE TABLE IF NOT EXISTS ingested_files (
    file_hash TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('holdings','transactions')),
    rows_loaded INTEGER NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_main ON transactions(person, symbol, trade_date);
CREATE INDEX IF NOT EXISTS idx_lots_main ON lots(person, symbol, buy_date);
CREATE INDEX IF NOT EXISTS idx_matches_main ON matches(person, symbol, sell_date);
"""


def connect():
    c = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    c.execute("PRAGMA foreign_keys = ON")
    return c


def init_db():
    c = connect()
    c.executescript(SCHEMA)
    c.close()


def execute(query, params=()):
    """Run one write query in its own transaction and return the last inserted id."""
    c = connect()
    with c:
        cur = c.execute(query, params)
    c.close()
    return cur.lastrowid


def fetch_df(query, params=()):
    """Run a read query and return a DataFrame."""
    c = connect()
    df = pd.read_sql_query(query, c, params=params)
    c.close()
    return df
