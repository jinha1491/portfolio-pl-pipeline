import datetime as dt
import os
from pathlib import Path

import psycopg
import pytest

from portfolio import db, ingest

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/portfolio_test",
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the package at an emptied test database and a throwaway data folder."""
    assert "test" in TEST_DATABASE_URL.rsplit("/", 1)[-1], "tests wipe the database, so it must be a test one"
    with psycopg.connect(TEST_DATABASE_URL) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    monkeypatch.setattr(db, "DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setattr(ingest, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(ingest, "PROCESSED", tmp_path / "processed")
    monkeypatch.setattr(ingest, "FAILED", tmp_path / "failed")
    db.init_db()
    return tmp_path


def drop(store, kind, sample, name):
    """Copy a sample file into the test inbox under a new name."""
    folder = store / "inbox" / kind
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes((SAMPLES / sample).read_bytes())


def add_trade(person, symbol, side, trade_date, qty, price, fees=0.0):
    with db.connect() as c:
        c.execute(
            """
            INSERT INTO transactions (person, symbol, side, trade_date, quantity, price, fees, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (person, symbol, side, dt.date.fromisoformat(trade_date), qty, price, fees,
             dt.datetime.now(dt.timezone.utc)),
        )
