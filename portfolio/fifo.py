import datetime as dt

from portfolio.db import connect

EPS = 1e-9


def term_class(buy_date: str, sell_date: str) -> str:
    """365+ days held is long-term, otherwise short-term."""
    days = (dt.date.fromisoformat(sell_date) - dt.date.fromisoformat(buy_date)).days
    return "LONG" if days >= 365 else "SHORT"


def _match_sell(cur, sell_txn_id, person, symbol, sell_date, sell_qty, sell_price, sell_fees):
    """Consume open BUY lots oldest-first for one SELL and record the matches."""
    lots = cur.execute(
        """
        SELECT id, buy_date, buy_price, remaining_qty
        FROM lots
        WHERE person = ? AND symbol = ? AND remaining_qty > ?
        ORDER BY buy_date ASC, id ASC
        """,
        (person, symbol, EPS),
    ).fetchall()

    remaining = sell_qty
    staged = []
    for lot_id, buy_date, buy_price, lot_rem in lots:
        if remaining <= EPS:
            break
        take = min(lot_rem, remaining)
        staged.append((lot_id, buy_date, buy_price, take, lot_rem - take))
        remaining -= take

    if remaining > EPS:
        raise ValueError(
            f"Not enough shares for FIFO: person={person}, symbol={symbol}, "
            f"sell_txn_id={sell_txn_id}, missing {remaining:.6f} shares."
        )

    fee_per_share = sell_fees / sell_qty
    for lot_id, buy_date, buy_price, take, lot_rem_after in staged:
        realized = (sell_price - buy_price) * take - fee_per_share * take
        cur.execute(
            """
            INSERT INTO matches
            (sell_txn_id, buy_lot_id, person, symbol, buy_date, sell_date,
             matched_qty, buy_price, sell_price, realized_pl, term)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sell_txn_id, lot_id, person, symbol, buy_date, sell_date,
             take, buy_price, sell_price, realized, term_class(buy_date, sell_date)),
        )
        cur.execute("UPDATE lots SET remaining_qty = ? WHERE id = ?", (lot_rem_after, lot_id))


def rebuild_fifo():
    """
    Rebuild lots and matches from the transactions ledger in a single transaction.
    If any SELL fails to match, everything rolls back and the old tables stay intact.
    """
    c = connect()
    try:
        with c:
            cur = c.cursor()
            cur.execute("DELETE FROM matches")
            cur.execute("DELETE FROM lots")

            rows = cur.execute(
                """
                SELECT id, TRIM(person), UPPER(TRIM(symbol)), side, trade_date, quantity, price, fees
                FROM transactions
                WHERE symbol IS NOT NULL AND TRIM(symbol) <> ''
                ORDER BY trade_date ASC, id ASC
                """
            ).fetchall()

            for tx_id, person, symbol, side, trade_date, qty, price, fees in rows:
                if side == "BUY":
                    cur.execute(
                        """
                        INSERT INTO lots (buy_txn_id, person, symbol, buy_date, buy_price, remaining_qty)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (tx_id, person, symbol, trade_date, price, qty),
                    )
                else:
                    _match_sell(cur, tx_id, person, symbol, trade_date, qty, price, fees)

            n_lots = cur.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
            n_matches = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    finally:
        c.close()

    return {"lots": n_lots, "matches": n_matches}
