from pathlib import Path

import pytest

from portfolio import db, ingest

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the package at a throwaway database and data folder for each test."""
    monkeypatch.setattr(db, "DB_FILE", str(tmp_path / "test.db"))
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
    c = db.connect()
    with c:
        c.execute(
            """
            INSERT INTO transactions (person, symbol, side, trade_date, quantity, price, fees, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'test')
            """,
            (person, symbol, side, trade_date, qty, price, fees),
        )
    c.close()
