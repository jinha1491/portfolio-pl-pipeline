import pytest

from conftest import add_trade
from portfolio.db import fetch_df
from portfolio.fifo import rebuild_fifo


def test_sell_consumes_oldest_lot_first(store):
    add_trade("John", "AAPL", "BUY", "2024-01-10", 10, 150)
    add_trade("John", "AAPL", "BUY", "2024-06-01", 5, 170)
    add_trade("John", "AAPL", "SELL", "2025-02-01", 12, 190, fees=2.4)

    rebuild_fifo()
    m = fetch_df("SELECT matched_qty, buy_price, realized_pl, term FROM matches ORDER BY id")

    # 10 shares from the January lot, then 2 from the June lot; fees split 0.2 per share
    assert m["matched_qty"].tolist() == [10, 2]
    assert m["buy_price"].tolist() == [150, 170]
    assert m["realized_pl"].round(2).tolist() == [398.0, 39.6]
    assert m["term"].tolist() == ["LONG", "SHORT"]

    remaining = fetch_df("SELECT remaining_qty FROM lots ORDER BY buy_date")["remaining_qty"].tolist()
    assert remaining == [0, 3]


def test_failed_rebuild_leaves_previous_tables(store):
    add_trade("Jane", "MSFT", "BUY", "2023-05-15", 10, 280)
    add_trade("Jane", "MSFT", "SELL", "2024-08-20", 5, 330)
    assert rebuild_fifo() == {"lots": 1, "matches": 1}

    add_trade("Jane", "MSFT", "SELL", "2025-01-01", 50, 300)
    with pytest.raises(ValueError, match="Not enough shares"):
        rebuild_fifo()

    assert len(fetch_df("SELECT * FROM lots")) == 1
    assert len(fetch_df("SELECT * FROM matches")) == 1
