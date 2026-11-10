"""
Load the sample Excel files and two weeks of made-up prices into the database
DATABASE_URL points at. CI uses this so dbt has data to build and test against.
Doesn't call Yahoo, so it runs offline and gives the same rows every time.
"""
import datetime as dt
from pathlib import Path

from portfolio import db, prices
from portfolio.fifo import rebuild_fifo
from portfolio.ingest import backfill_symbols, ingest_file

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"
BASE = {"AAPL": 200.0, "MSFT": 400.0, "TSLA": 250.0}


def main():
    db.init_db()
    ingest_file(SAMPLES / "sample_holdings.xlsx", "holdings")
    ingest_file(SAMPLES / "sample_transactions.xlsx", "transactions")
    backfill_symbols()
    rebuild_fifo()

    day, days = dt.date(2026, 10, 19), 0
    while day <= dt.date(2026, 10, 30):
        if day.weekday() < 5:
            closes = {sym: px * (1 + 0.01 * days) for sym, px in BASE.items()}
            prices.load_closes(day, closes)
            prices.snapshot_portfolio(day)
            days += 1
        day += dt.timedelta(days=1)
    print(f"loaded sample trades and {days} days of prices")


if __name__ == "__main__":
    main()
