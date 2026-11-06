import datetime as dt

import pytest

from conftest import drop
from portfolio import prices, quality
from portfolio.db import fetch_df
from portfolio.ingest import process_inbox
from portfolio.positions import open_positions

FAKE_CLOSES = {"SPY": 500.0, "AAPL": 200.0, "MSFT": 400.0}


@pytest.fixture
def loaded(store, monkeypatch):
    """Sample trades loaded, and Yahoo replaced with fixed prices on weekdays."""
    drop(store, "transactions", "sample_transactions.xlsx", "trades.xlsx")
    process_inbox()
    monkeypatch.setattr(
        prices, "fetch_close",
        lambda symbol, day: None if day.weekday() >= 5 else FAKE_CLOSES.get(symbol),
    )
    return store


def test_positions_reflect_trades_up_to_each_date(loaded):
    def as_dict(day):
        return {(p, s): q for p, s, q in open_positions(day)}

    assert as_dict(dt.date(2024, 8, 19)) == {("Jane Smith", "MSFT"): 10, ("John Doe", "AAPL"): 15}
    assert as_dict(dt.date(2024, 8, 21)) == {("Jane Smith", "MSFT"): 5, ("John Doe", "AAPL"): 15}
    assert as_dict(dt.date(2026, 11, 5)) == {("Jane Smith", "MSFT"): 5, ("John Doe", "AAPL"): 7}
    assert as_dict(dt.date(2023, 1, 3)) == {}


def test_rerunning_a_day_gives_the_same_rows(loaded):
    day = dt.date(2026, 11, 5)
    for _ in range(2):
        prices.load_closes(day, prices.extract_closes(day))
        prices.snapshot_portfolio(day)

    assert len(fetch_df("SELECT * FROM daily_prices")) == 2
    snap = fetch_df("SELECT person, market_value FROM portfolio_snapshots ORDER BY person")
    assert snap.values.tolist() == [["Jane Smith", 2000.0], ["John Doe", 1400.0]]
    assert quality.check_snapshot_complete(day).passed


def test_weekend_counts_as_market_closed(loaded):
    assert prices.extract_closes(dt.date(2026, 11, 7)) is None


def test_missing_price_on_an_open_day_raises(loaded, monkeypatch):
    monkeypatch.setitem(FAKE_CLOSES, "MSFT", None)
    with pytest.raises(RuntimeError, match="MSFT"):
        prices.extract_closes(dt.date(2026, 11, 5))


def test_empty_ledger_fails_the_snapshot_check(store):
    assert not quality.check_snapshot_complete(dt.date(2026, 11, 5)).passed
