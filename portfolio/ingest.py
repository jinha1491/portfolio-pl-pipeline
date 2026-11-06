import datetime as dt
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import pandas as pd

from portfolio.db import connect, init_db

DATA_DIR = Path(os.environ.get(
    "PORTFOLIO_DATA",
    Path(__file__).resolve().parent.parent / "data",
))
INBOX = DATA_DIR / "inbox"
PROCESSED = DATA_DIR / "processed"
FAILED = DATA_DIR / "failed"

# Holdings go first so the symbol backfill can map stock names in new trades.
KINDS = ("holdings", "transactions")
DEFAULT_SHEETS = {"holdings": "details", "transactions": "Sheet2"}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def file_hash(path):
    """SHA-256 of the file's bytes. Same content means same hash, whatever the file is called."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_sheet(path, kind):
    """Read the expected sheet, or the first sheet if that name isn't there."""
    xl = pd.ExcelFile(path)
    sheet = DEFAULT_SHEETS[kind]
    if sheet not in xl.sheet_names:
        sheet = xl.sheet_names[0]
    return pd.read_excel(xl, sheet_name=sheet, header=None)


def _is_blank(x):
    return x is None or (isinstance(x, float) and pd.isna(x)) or (isinstance(x, str) and x.strip() == "")


def _to_date_str(x):
    """Excel dates arrive as datetimes or as strings like 2025-05-14 or 2025.05.14."""
    if isinstance(x, dt.datetime):
        return x.date().isoformat()
    if isinstance(x, dt.date):
        return x.isoformat()
    s = str(x).strip().replace(".", "-")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    try:
        return pd.to_datetime(s).date().isoformat()
    except Exception:
        raise ValueError(f"Unrecognized date value: {x}")


# ---------- parsers: file -> rows, no database access ----------

def parse_holdings(path):
    raw = _read_sheet(path, "holdings")

    header_row = None
    for i in range(min(len(raw), 50)):
        row = [str(x).strip() for x in raw.iloc[i]]
        if "Stock Name" in row and "symbol" in row and "Quantity(sum)" in row:
            header_row = i
            break
    if header_row is None:
        raise ValueError("Could not find holdings header row (Stock Name / symbol / Quantity(sum)).")

    df = raw.iloc[header_row + 1:].copy()
    df.columns = [str(x).strip() for x in raw.iloc[header_row]]
    df = df.dropna(subset=["Stock Name", "symbol"])
    account_cols = [c for c in df.columns if c.lower().startswith("account")]

    rows = []
    for _, r in df.iterrows():
        qty = r["Quantity(sum)"]
        rows.append((
            str(r["Stock Name"]).strip(),
            str(r["symbol"]).strip().upper(),
            float(qty) if pd.notna(qty) else 0.0,
            json.dumps([float(r[c]) if pd.notna(r[c]) else 0.0 for c in account_cols]),
        ))
    return rows


def _read_trade_block(raw, start, header, side, person):
    """Read trade rows under one BUY or SELL header until the first blank Stock Name."""
    qty_col = "buy quantity" if side == "BUY" else "selling quantity"
    date_col = "Buy date" if side == "BUY" else "selling date"
    col_stock = header.index("Stock Name")
    col_qty = header.index(qty_col)
    col_date = header.index(date_col)
    col_price = header.index("Unit Price")
    col_symbol = header.index("symbol") if "symbol" in header else None
    col_fees = next((k for k, v in enumerate(header) if "fees" in v.lower()), None)

    rows = []
    j = start
    while j < len(raw) and not _is_blank(raw.iat[j, col_stock]):
        sym = raw.iat[j, col_symbol] if col_symbol is not None else None
        fees = raw.iat[j, col_fees] if col_fees is not None else 0.0
        rows.append((
            person,
            None if _is_blank(sym) else str(sym).strip().upper(),
            str(raw.iat[j, col_stock]).strip(),
            side,
            _to_date_str(raw.iat[j, col_date]),
            float(raw.iat[j, col_qty]),
            float(raw.iat[j, col_price]),
            float(fees) if pd.notna(fees) else 0.0,
        ))
        j += 1
    return rows, j


def parse_transactions(path):
    """
    Layout: a "Trader: Name" line, then a BUY table and a SELL table, repeated per trader.
    BUY header: Stock Name, buy quantity, Buy date, Unit Price (optional symbol, fees)
    SELL header: Stock Name, selling quantity, selling date, Unit Price (optional symbol, fees)
    """
    raw = _read_sheet(path, "transactions")
    person = "Unknown"
    rows = []
    i = 0
    while i < len(raw):
        cell0 = raw.iat[i, 0]
        if isinstance(cell0, str) and "Trader" in cell0:
            parts = cell0.split(":", 1)
            person = parts[1].strip() if len(parts) == 2 else cell0.strip()
            i += 1
            continue

        header = [str(x).strip() for x in raw.iloc[i]]
        side = None
        if "Stock Name" in header and "buy quantity" in header and "Buy date" in header:
            side = "BUY"
        elif "Stock Name" in header and "selling quantity" in header and "selling date" in header:
            side = "SELL"

        if side:
            block, i = _read_trade_block(raw, i + 1, header, side, person)
            rows.extend(block)
        else:
            i += 1
    return rows


# ---------- loaders: rows -> database, inside the caller's transaction ----------

def _load_holdings(c, rows):
    now = _now()
    c.executemany(
        """
        INSERT INTO holdings (stock_name, symbol, total_quantity, account_quantities, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            stock_name = excluded.stock_name,
            total_quantity = excluded.total_quantity,
            account_quantities = excluded.account_quantities,
            updated_at = excluded.updated_at
        """,
        [r + (now,) for r in rows],
    )


def _load_transactions(c, rows):
    now = _now()
    c.executemany(
        """
        INSERT INTO transactions
        (person, symbol, stock_name, side, trade_date, quantity, price, fees, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [r + (now,) for r in rows],
    )


PARSERS = {"holdings": parse_holdings, "transactions": parse_transactions}
LOADERS = {"holdings": _load_holdings, "transactions": _load_transactions}


def ingest_file(path, kind):
    """
    Load one file and record its hash in the same transaction.
    Returns ("loaded", n) or ("duplicate", 0). Raises if the file can't be parsed.
    """
    digest = file_hash(path)
    c = connect()
    try:
        with c:
            if c.execute("SELECT 1 FROM ingested_files WHERE file_hash = ?", (digest,)).fetchone():
                return "duplicate", 0
            rows = PARSERS[kind](path)
            LOADERS[kind](c, rows)
            c.execute(
                """
                INSERT INTO ingested_files (file_hash, file_name, kind, rows_loaded, ingested_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (digest, path.name, kind, len(rows), _now()),
            )
            return "loaded", len(rows)
    finally:
        c.close()


def backfill_symbols():
    """Fill in missing trade symbols by matching stock names against holdings."""
    c = connect()
    with c:
        cur = c.execute(
            """
            UPDATE transactions
            SET symbol = (SELECT h.symbol FROM holdings h WHERE h.stock_name = TRIM(transactions.stock_name))
            WHERE (symbol IS NULL OR TRIM(symbol) = '')
              AND TRIM(stock_name) IN (SELECT stock_name FROM holdings)
            """
        )
        n = cur.rowcount
    c.close()
    return n


def _move(path, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / path.name
    if target.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        target = dest_dir / f"{path.stem}_{stamp}{path.suffix}"
    shutil.move(str(path), str(target))


def save_upload(kind, name, data):
    """Write an uploaded file into the inbox. The timestamp keeps two uploads from colliding."""
    folder = INBOX / kind
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = folder / f"{stamp}_{name}"
    path.write_bytes(data)
    return path


def process_inbox():
    """
    Ingest every file in the inbox. Good files and duplicates move to processed/,
    files that fail to parse move to failed/ so they don't block the next run.
    """
    init_db()
    files, failed = [], []
    for kind in KINDS:
        folder = INBOX / kind
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(folder.glob("*.xlsx")):
            try:
                status, n = ingest_file(path, kind)
            except Exception as e:
                _move(path, FAILED / kind)
                failed.append(f"{kind}/{path.name}: {e}")
                continue
            _move(path, PROCESSED / kind)
            files.append((path.name, kind, status, n))

    return {"files": files, "failed": failed, "symbols_backfilled": backfill_symbols()}
