from dataclasses import dataclass

from portfolio.db import connect
from portfolio.positions import POSITIONS_AS_OF


@dataclass
class CheckResult:
    name: str
    severity: str  # "error" fails the run, "warn" only gets logged
    passed: bool
    detail: str


class DataQualityError(Exception):
    pass


def _rows(query, params=()):
    c = connect()
    rows = c.execute(query, params).fetchall()
    c.close()
    return rows


def check_prices_present(day):
    missing = _rows(
        f"""
        SELECT DISTINCT pos.symbol
        FROM ({POSITIONS_AS_OF}) AS pos
        LEFT JOIN daily_prices p ON p.symbol = pos.symbol AND p.price_date = :day
        WHERE p.symbol IS NULL
        """,
        {"day": day.isoformat()},
    )
    return CheckResult(
        "prices_present", "error", not missing,
        f"missing prices for {[r[0] for r in missing]}" if missing else "all held symbols priced",
    )


def check_price_moves(day, threshold=0.5):
    """Flags day-over-day moves above 50%. Usually a split or bad data, not a real move."""
    jumps = _rows(
        """
        SELECT symbol, prev_close, close
        FROM (
            SELECT symbol, price_date, close,
                   LAG(close) OVER (PARTITION BY symbol ORDER BY price_date) AS prev_close
            FROM daily_prices
        )
        WHERE price_date = ?
          AND prev_close IS NOT NULL
          AND ABS(close - prev_close) / prev_close > ?
        """,
        (day.isoformat(), threshold),
    )
    return CheckResult(
        "price_moves", "warn", not jumps,
        f"large moves: {jumps}" if jumps else "no moves above threshold",
    )


def check_snapshot_complete(day):
    """Every open position has a snapshot row. An empty ledger fails, since that means nothing was loaded."""
    trades, held, snapped = _rows(
        f"""
        SELECT
          (SELECT COUNT(*) FROM transactions),
          (SELECT COUNT(*) FROM ({POSITIONS_AS_OF})),
          (SELECT COUNT(*) FROM portfolio_snapshots WHERE snapshot_date = :day)
        """,
        {"day": day.isoformat()},
    )[0]
    return CheckResult(
        "snapshot_complete", "error", trades > 0 and held == snapped,
        f"{snapped} of {held} open positions in snapshot" if trades else "ledger is empty",
    )


def check_duplicate_trades():
    """Importing the same Excel file twice doubles every trade. This catches it."""
    dupes = _rows(
        """
        SELECT person, symbol, side, trade_date, quantity, price, COUNT(*) AS n
        FROM transactions
        GROUP BY person, symbol, side, trade_date, quantity, price, fees
        HAVING COUNT(*) > 1
        """
    )
    return CheckResult(
        "duplicate_trades", "warn", not dupes,
        f"{len(dupes)} repeated trade rows, e.g. {dupes[0]}" if dupes else "no repeated trades",
    )


def check_holdings_match_ledger():
    """Holdings come from a separate import, so they can drift from the trade ledger."""
    gaps = _rows(
        """
        SELECT h.symbol, h.total_quantity,
               COALESCE(SUM(CASE t.side WHEN 'BUY' THEN t.quantity ELSE -t.quantity END), 0) AS ledger_qty
        FROM holdings h
        LEFT JOIN transactions t ON UPPER(TRIM(t.symbol)) = h.symbol
        GROUP BY h.symbol, h.total_quantity
        HAVING ABS(h.total_quantity - ledger_qty) > 1e-9
        """
    )
    return CheckResult(
        "holdings_match_ledger", "warn", not gaps,
        f"holdings vs ledger (symbol, holdings, ledger): {gaps}" if gaps else "holdings match ledger",
    )


def price_checks(day):
    return [check_prices_present(day), check_price_moves(day)]


def output_checks(day):
    return [check_snapshot_complete(day), check_duplicate_trades(), check_holdings_match_ledger()]


def enforce(results, log=print):
    """Log every result, then raise if any error-level check failed."""
    for r in results:
        status = "PASS" if r.passed else ("FAIL" if r.severity == "error" else "WARN")
        log(f"[{status}] {r.name}: {r.detail}")
    failed = [r.name for r in results if r.severity == "error" and not r.passed]
    if failed:
        raise DataQualityError(f"failed checks: {failed}")
