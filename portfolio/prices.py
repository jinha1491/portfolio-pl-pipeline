import datetime as dt

import yfinance as yf

from portfolio.db import connect

# If SPY has no bar for a day, the market was closed (weekend or holiday).
MARKET_PROXY = "SPY"


def held_symbols():
    c = connect()
    rows = c.execute(
        "SELECT symbol FROM holdings WHERE total_quantity > 0 ORDER BY symbol"
    ).fetchall()
    c.close()
    return [r[0] for r in rows]


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
    Returns {symbol: close} for every held symbol, or None if the market was closed.
    Raises if the market was open but some symbols came back empty, so the task retries.
    """
    if fetch_close(MARKET_PROXY, day) is None:
        return None

    symbols = held_symbols()
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
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = [(sym, day.isoformat(), px, now) for sym, px in closes.items()]
    c = connect()
    with c:
        c.executemany(
            """
            INSERT INTO daily_prices (symbol, price_date, close, loaded_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, price_date) DO UPDATE SET
                close = excluded.close,
                loaded_at = excluded.loaded_at
            """,
            rows,
        )
    c.close()
    return len(rows)


def snapshot_portfolio(day):
    """Replace one day's portfolio snapshot: delete that date, then insert it fresh."""
    d = day.isoformat()
    c = connect()
    with c:
        c.execute("DELETE FROM portfolio_snapshots WHERE snapshot_date = ?", (d,))
        cur = c.execute(
            """
            INSERT INTO portfolio_snapshots (snapshot_date, symbol, quantity, close, market_value)
            SELECT ?, h.symbol, h.total_quantity, p.close, h.total_quantity * p.close
            FROM holdings h
            JOIN daily_prices p ON p.symbol = h.symbol AND p.price_date = ?
            WHERE h.total_quantity > 0
            """,
            (d, d),
        )
        n = cur.rowcount
    c.close()
    return n
