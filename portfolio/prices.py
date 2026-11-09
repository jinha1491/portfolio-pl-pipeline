import datetime as dt

import yfinance as yf

from portfolio.db import connect
from portfolio.positions import POSITIONS_AS_OF, held_symbols

# If SPY has no bar for a day, the market was closed (weekend or holiday).
MARKET_PROXY = "SPY"


def fetch_close(symbol, day):
    """Unadjusted closing price for one symbol on one day, or None if Yahoo has no bar."""
    hist = yf.Ticker(symbol).history(
        start=day.isoformat(),
        end=(day + dt.timedelta(days=1)).isoformat(),
        auto_adjust=False,
    )
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def extract_closes(day):
    """
    Returns {symbol: close} for every symbol held on that day, or None if the market was closed.
    Raises if the market was open but some symbols came back empty, so the task retries.
    """
    if fetch_close(MARKET_PROXY, day) is None:
        return None

    symbols = held_symbols(day)
    closes = {}
    for sym in symbols:
        px = fetch_close(sym, day)
        if px is not None:
            closes[sym] = px

    missing = sorted(set(symbols) - set(closes))
    if missing:
        raise RuntimeError(f"No close for {missing} on {day} even though the market was open")
    return closes


def load_closes(day, closes):
    """Upsert closes for one day. Running it twice for the same day leaves the same rows."""
    now = dt.datetime.now(dt.timezone.utc)
    rows = [(sym, day, px, now) for sym, px in closes.items()]
    c = connect()
    with c:
        c.cursor().executemany(
            """
            INSERT INTO daily_prices (symbol, price_date, close, loaded_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(symbol, price_date) DO UPDATE SET
                close = excluded.close,
                loaded_at = excluded.loaded_at
            """,
            rows,
        )
    c.close()
    return len(rows)


def snapshot_portfolio(day):
    """Replace one day's snapshot with positions as of that day, valued at that day's close."""
    c = connect()
    with c:
        c.execute("DELETE FROM portfolio_snapshots WHERE snapshot_date = %(day)s", {"day": day})
        cur = c.execute(
            f"""
            INSERT INTO portfolio_snapshots (snapshot_date, person, symbol, quantity, close, market_value)
            SELECT %(day)s, pos.person, pos.symbol, pos.quantity, p.close, pos.quantity * p.close
            FROM ({POSITIONS_AS_OF}) AS pos
            JOIN daily_prices p ON p.symbol = pos.symbol AND p.price_date = %(day)s
            """,
            {"day": day},
        )
        n = cur.rowcount
    c.close()
    return n
