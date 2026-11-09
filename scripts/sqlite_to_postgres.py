"""
One-time copy of the old SQLite database into Postgres.

    python scripts/sqlite_to_postgres.py portfolio.db

Copies every table with its original ids, moves the id sequences past the copied rows,
then checks row counts and column sums on both sides. Refuses to run if Postgres
already has data, so it can't double-load.
"""
import json
import sqlite3
import sys

from psycopg.types.json import Jsonb

from portfolio import db

# Parents before children so foreign keys are satisfied as rows go in.
TABLES = {
    "holdings": ["id", "stock_name", "symbol", "total_quantity", "account_quantities", "updated_at"],
    "transactions": ["id", "person", "symbol", "stock_name", "side", "trade_date",
                     "quantity", "price", "fees", "created_at"],
    "lots": ["id", "buy_txn_id", "person", "symbol", "buy_date", "buy_price", "remaining_qty"],
    "matches": ["id", "sell_txn_id", "buy_lot_id", "person", "symbol", "buy_date", "sell_date",
                "matched_qty", "buy_price", "sell_price", "realized_pl", "term"],
    "daily_prices": ["symbol", "price_date", "close", "loaded_at"],
    "portfolio_snapshots": ["snapshot_date", "person", "symbol", "quantity", "close", "market_value"],
    "ingested_files": ["file_hash", "file_name", "kind", "rows_loaded", "ingested_at"],
}
WITH_IDS = ["holdings", "transactions", "lots", "matches"]
SUMS = {
    "transactions": "quantity",
    "lots": "remaining_qty",
    "matches": "realized_pl",
    "daily_prices": "close",
    "portfolio_snapshots": "market_value",
}


def main(sqlite_path):
    src = sqlite3.connect(sqlite_path)
    db.init_db()

    with db.connect() as pg:
        cur = pg.cursor()
        for table in TABLES:
            if cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                sys.exit(f"Postgres table {table} already has rows. Not copying.")

        for table, cols in TABLES.items():
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            if table == "holdings":
                rows = [r[:4] + (Jsonb(json.loads(r[4])),) + r[5:] for r in rows]
            marks = ", ".join(["%s"] * len(cols))
            cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", rows)

        for table in WITH_IDS:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE(MAX(id), 0) + 1, false) FROM {table}"
            )

        problems = []
        for table in TABLES:
            a = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            b = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table:<20} sqlite {a:>7}  postgres {b:>7}")
            if a != b:
                problems.append(f"{table}: {a} rows in SQLite, {b} in Postgres")
        for table, col in SUMS.items():
            a = src.execute(f"SELECT COALESCE(SUM({col}), 0) FROM {table}").fetchone()[0]
            b = cur.execute(f"SELECT COALESCE(SUM({col}), 0) FROM {table}").fetchone()[0]
            if abs(a - b) > 1e-6 * max(1.0, abs(a)):
                problems.append(f"{table}.{col}: sum {a} in SQLite, {b} in Postgres")

        if problems:
            raise RuntimeError("copy didn't match, rolled back:\n" + "\n".join(problems))
    print("All row counts and sums match.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/sqlite_to_postgres.py path/to/portfolio.db")
    main(sys.argv[1])
