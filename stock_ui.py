import streamlit as st
import pandas as pd
import sqlite3
import datetime as dt
import yfinance as yf
import json
import re

# Local SQLite database file (created automatically if it doesn't exist)
DB_FILE = "portfolio.db"

st.set_page_config(page_title="Portfolio (USD) + FIFO Realized P/L", layout="wide")
st.title("Portfolio Analytics (USD) with Excel Import, SQLite, FIFO Realized P/L")

# ============================================================
# 1) SQLite layer
#    Purpose: keep everything persistent (no CSV files)
#    Tables:
#      - holdings: current snapshot of what you own (with per-account quantities)
#      - transactions: BUY/SELL ledger (history)
#      - lots: FIFO lots derived from BUYs
#      - matches: FIFO matches linking SELLs to BUY lots (realized P/L)
# ============================================================

def conn():
    # check_same_thread=False lets Streamlit reuse the connection across reruns safely
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    c = conn()
    cur = c.cursor()

    # holdings: portfolio snapshot
    # account_quantities = JSON list like [acct1_qty, acct2_qty, ...]
    cur.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_name TEXT NOT NULL,
            symbol TEXT NOT NULL UNIQUE,
            total_quantity REAL NOT NULL,
            account_quantities TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # transactions: trade history / ledger
    # symbol can be NULL because some transaction sheets may only have Stock Name
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person TEXT NOT NULL,
            symbol TEXT,
            stock_name TEXT,
            side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
            trade_date TEXT NOT NULL,           -- YYYY-MM-DD
            quantity REAL NOT NULL CHECK(quantity > 0),
            price REAL NOT NULL CHECK(price >= 0),
            fees REAL NOT NULL DEFAULT 0 CHECK(fees >= 0),
            created_at TEXT NOT NULL
        )
    """)

    # lots: derived from BUY transactions (used for FIFO matching)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buy_txn_id INTEGER NOT NULL,
            person TEXT NOT NULL,
            symbol TEXT NOT NULL,
            buy_date TEXT NOT NULL,             -- YYYY-MM-DD
            buy_price REAL NOT NULL,
            remaining_qty REAL NOT NULL,
            FOREIGN KEY(buy_txn_id) REFERENCES transactions(id)
        )
    """)

    # matches: derived from SELL transactions matched against FIFO lots
    # realized_pl = (sell_price - buy_price) * matched_qty - fees_allocated
    cur.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sell_txn_id INTEGER NOT NULL,
            buy_lot_id INTEGER NOT NULL,
            person TEXT NOT NULL,
            symbol TEXT NOT NULL,
            buy_date TEXT NOT NULL,
            sell_date TEXT NOT NULL,
            matched_qty REAL NOT NULL,
            buy_price REAL NOT NULL,
            sell_price REAL NOT NULL,
            realized_pl REAL NOT NULL,
            term TEXT NOT NULL CHECK(term IN ('SHORT','LONG')),
            FOREIGN KEY(sell_txn_id) REFERENCES transactions(id),
            FOREIGN KEY(buy_lot_id) REFERENCES lots(id)
        )
    """)

    # indexes speed up common lookups (helpful as data grows)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_txn_main ON transactions(person, symbol, trade_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lots_main ON lots(person, symbol, buy_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_matches_main ON matches(person, symbol, sell_date)")

    c.commit()
    c.close()

def execute(query, params=()):
    """Run a write query (INSERT/UPDATE/DELETE) and return last inserted id."""
    c = conn()
    cur = c.cursor()
    cur.execute(query, params)
    c.commit()
    last_id = cur.lastrowid
    c.close()
    return last_id

def fetch_df(query, params=()):
    """Run a read query and return as a DataFrame."""
    c = conn()
    df = pd.read_sql_query(query, c, params=params)
    c.close()
    return df

init_db()

# ============================================================
# 2) FIFO realized P/L logic
#    Key idea:
#      - BUY creates a "lot" with remaining_qty
#      - SELL consumes lots in chronological order (FIFO)
#      - each SELL gets broken into match records (SELL matched to BUY lots)
# ============================================================

def holding_days(buy_date_str: str, sell_date_str: str) -> int:
    """Number of days between buy and sell (used for short/long term classification)."""
    b = dt.date.fromisoformat(buy_date_str)
    s = dt.date.fromisoformat(sell_date_str)
    return (s - b).days

def term_class(buy_date_str: str, sell_date_str: str) -> str:
    """US-style rule of thumb: >= 365 days is long-term, otherwise short-term."""
    return "LONG" if holding_days(buy_date_str, sell_date_str) >= 365 else "SHORT"

def fifo_match_sell_for_rebuild(
    person: str,
    symbol: str,
    sell_txn_id: int,
    sell_date: str,
    sell_qty: float,
    sell_price: float,
    sell_fees: float
):
    """
    Match one SELL transaction against existing BUY lots using FIFO.
    This writes rows into `matches` and updates `lots.remaining_qty`.
    """

    # Pull lots oldest-first
    lots_df = fetch_df("""
        SELECT id, buy_date, buy_price, remaining_qty
        FROM lots
        WHERE person = ? AND symbol = ? AND remaining_qty > 0
        ORDER BY buy_date ASC, id ASC
    """, (person, symbol))

    if lots_df.empty:
        raise ValueError(f"No available BUY lots for FIFO matching: person={person}, symbol={symbol}")

    remaining_to_sell = float(sell_qty)
    total_matched = 0.0

    # We'll compute the allocation first, then write it (safer than writing mid-loop)
    staged = []  # (lot_id, buy_date, buy_price, matched_qty, lot_remaining_after)

    for _, lot in lots_df.iterrows():
        if remaining_to_sell <= 1e-12:
            break

        lot_id = int(lot["id"])
        buy_date = str(lot["buy_date"])
        buy_price = float(lot["buy_price"])
        lot_rem = float(lot["remaining_qty"])

        match_qty = min(lot_rem, remaining_to_sell)
        staged.append((lot_id, buy_date, buy_price, match_qty, lot_rem - match_qty))

        remaining_to_sell -= match_qty
        total_matched += match_qty

    # If you try to sell more shares than you have in lots, we throw an error
    if remaining_to_sell > 1e-9:
        raise ValueError(
            f"Not enough shares to sell for FIFO: person={person}, symbol={symbol}. "
            f"Missing {remaining_to_sell:.6f} shares."
        )

    # Allocate fees proportionally across matched shares (fee per share)
    fee_per_share = (float(sell_fees) / total_matched) if total_matched > 0 else 0.0

    # Write match rows + update lot balances
    for lot_id, buy_date, buy_price, match_qty, lot_rem_after in staged:
        realized = (float(sell_price) - buy_price) * match_qty - (fee_per_share * match_qty)
        term = term_class(buy_date, sell_date)

        execute("""
            INSERT INTO matches
            (sell_txn_id, buy_lot_id, person, symbol, buy_date, sell_date, matched_qty,
             buy_price, sell_price, realized_pl, term)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sell_txn_id, lot_id, person, symbol,
            buy_date, sell_date, float(match_qty),
            float(buy_price), float(sell_price),
            float(realized), term
        ))

        execute("UPDATE lots SET remaining_qty = ? WHERE id = ?", (float(lot_rem_after), lot_id))

def rebuild_fifo():
    """
    IMPORTANT: lots + matches are derived tables.
    We rebuild them from the source-of-truth ledger: `transactions`.

    Steps:
      1) clear lots/matches
      2) replay transactions in chronological order
      3) BUY -> create a lot
      4) SELL -> match against existing lots FIFO
    """
    execute("DELETE FROM matches")
    execute("DELETE FROM lots")

    tx = fetch_df("""
        SELECT id, person, symbol, side, trade_date, quantity, price, fees
        FROM transactions
        WHERE symbol IS NOT NULL AND TRIM(symbol) <> ''
        ORDER BY trade_date ASC, id ASC
    """)

    for _, r in tx.iterrows():
        tx_id = int(r["id"])
        person = str(r["person"]).strip()
        symbol = str(r["symbol"]).strip().upper()
        side = str(r["side"]).strip().upper()
        trade_date = str(r["trade_date"]).strip()
        qty = float(r["quantity"])
        price = float(r["price"])
        fees = float(r["fees"])

        if side == "BUY":
            # Create a new FIFO lot
            execute("""
                INSERT INTO lots (buy_txn_id, person, symbol, buy_date, buy_price, remaining_qty)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (tx_id, person, symbol, trade_date, price, qty))
        else:
            # Consume lots FIFO and record realized P/L
            fifo_match_sell_for_rebuild(
                person=person,
                symbol=symbol,
                sell_txn_id=tx_id,
                sell_date=trade_date,
                sell_qty=qty,
                sell_price=price,
                sell_fees=fees
            )

# ============================================================
# 3) Transaction insert (ledger only)
#    We do NOT update lots/matches here.
#    We rebuild FIFO later so the logic is consistent and reproducible.
# ============================================================

def add_transaction(person, symbol, stock_name, side, trade_date, quantity, price, fees):
    now = dt.datetime.utcnow().isoformat()

    symbol_clean = (symbol or "").strip().upper()
    if symbol_clean == "":
        symbol_clean = None

    stock_name_clean = (stock_name or "").strip()
    if stock_name_clean == "":
        stock_name_clean = None

    return execute("""
        INSERT INTO transactions
        (person, symbol, stock_name, side, trade_date, quantity, price, fees, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        person.strip(),
        symbol_clean,
        stock_name_clean,
        side,
        trade_date,
        float(quantity),
        float(price),
        float(fees),
        now
    ))

# ============================================================
# 4) Excel imports
#    We accept Excel because a lot of portfolio tracking starts in spreadsheets.
#    Holdings import stores "what you own now".
#    Transactions import stores "what trades happened".
# ============================================================

def import_holdings_from_excel(file, sheet_name="details"):
    """
    Reads holdings sheet with a header row that includes:
    Stock Name | symbol | Quantity(sum) | Account 1..N
    We detect the header row because some files have titles above the table.
    """
    raw = pd.read_excel(file, sheet_name=sheet_name, header=None)
    header_row = None

    for i in range(min(len(raw), 50)):
        row = raw.iloc[i].astype(str).tolist()
        if "Stock Name" in row and "symbol" in row and "Quantity(sum)" in row:
            header_row = i
            break

    if header_row is None:
        raise ValueError("Could not find holdings header row (Stock Name / symbol / Quantity(sum)).")

    df = pd.read_excel(file, sheet_name=sheet_name, header=header_row)
    df = df.dropna(subset=["Stock Name", "symbol"])

    # Account columns can be Account 1..Account 6..etc
    account_cols = [c for c in df.columns if isinstance(c, str) and c.strip().lower().startswith("account")]

    # Basic validation
    for col in ["Stock Name", "symbol", "Quantity(sum)"]:
        if col not in df.columns:
            raise ValueError(f"Holdings sheet missing required column: {col}")

    now = dt.datetime.utcnow().isoformat()
    inserted = 0

    for _, r in df.iterrows():
        stock_name = str(r["Stock Name"]).strip()
        symbol = str(r["symbol"]).strip().upper()
        total_qty = float(r["Quantity(sum)"]) if pd.notna(r["Quantity(sum)"]) else 0.0

        acct_qty = []
        for c in account_cols:
            v = r.get(c)
            acct_qty.append(float(v) if pd.notna(v) else 0.0)

        # Upsert by symbol (update if already exists)
        execute("""
            INSERT INTO holdings (stock_name, symbol, total_quantity, account_quantities, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
              stock_name=excluded.stock_name,
              total_quantity=excluded.total_quantity,
              account_quantities=excluded.account_quantities,
              updated_at=excluded.updated_at
        """, (stock_name, symbol, total_qty, json.dumps(acct_qty), now))

        inserted += 1

    return inserted

def _is_blank(x):
    return x is None or (isinstance(x, float) and pd.isna(x)) or (isinstance(x, str) and x.strip() == "")

def _to_date_str(x):
    """
    Converts Excel date values to YYYY-MM-DD.
    Handles:
      - datetime objects
      - date objects
      - strings like 2025-05-14 or 2025.05.14
    """
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

def import_transactions_from_excel(file, sheet_name="Sheet2"):
    """
    Expected format:
      Trader: Name
      [BUY table]
      [SELL table]
      Trader: Another Name
      ...

    BUY header must include: Stock Name, buy quantity, Buy date, Unit Price (optional: symbol, fees)
    SELL header must include: Stock Name, selling quantity, selling date, Unit Price (optional: symbol, fees)
    """
    raw = pd.read_excel(file, sheet_name=sheet_name, header=None)

    current_person = "Unknown"
    inserted = 0
    i = 0

    while i < len(raw):
        cell0 = raw.iat[i, 0] if raw.shape[1] > 0 else None

        # Person block marker
        if isinstance(cell0, str) and "Trader" in cell0:
            parts = re.split(r":", cell0, maxsplit=1)
            current_person = parts[1].strip() if len(parts) == 2 else cell0.strip()
            i += 1
            continue

        row = raw.iloc[i].tolist()
        row_str = [str(x).strip() for x in row]

        is_buy_header = ("Stock Name" in row_str and "buy quantity" in row_str and "Buy date" in row_str)
        is_sell_header = ("Stock Name" in row_str and "selling quantity" in row_str and "selling date" in row_str)

        if is_buy_header:
            # Column index mapping based on header text
            col_stock = row_str.index("Stock Name")
            col_qty = row_str.index("buy quantity")
            col_date = row_str.index("Buy date")
            col_price = row_str.index("Unit Price")
            col_symbol = row_str.index("symbol") if "symbol" in row_str else None

            # fees column name can vary as long as it contains "fees"
            col_fees = None
            for k, v in enumerate(row_str):
                if "fees" in v.lower():
                    col_fees = k
                    break

            # Read until blank stock row
            j = i + 1
            while j < len(raw):
                stock = raw.iat[j, col_stock]
                if _is_blank(stock):
                    break

                sym = raw.iat[j, col_symbol] if col_symbol is not None else None
                qty = raw.iat[j, col_qty]
                buy_date = raw.iat[j, col_date]
                price = raw.iat[j, col_price]
                fees = raw.iat[j, col_fees] if col_fees is not None else 0.0

                add_transaction(
                    person=current_person,
                    symbol=str(sym).strip() if (col_symbol is not None and not _is_blank(sym)) else None,
                    stock_name=str(stock).strip(),
                    side="BUY",
                    trade_date=_to_date_str(buy_date),
                    quantity=float(qty),
                    price=float(price),
                    fees=float(fees) if pd.notna(fees) else 0.0
                )
                inserted += 1
                j += 1

            i = j
            continue

        if is_sell_header:
            col_stock = row_str.index("Stock Name")
            col_qty = row_str.index("selling quantity")
            col_date = row_str.index("selling date")
            col_price = row_str.index("Unit Price")
            col_symbol = row_str.index("symbol") if "symbol" in row_str else None

            col_fees = None
            for k, v in enumerate(row_str):
                if "fees" in v.lower():
                    col_fees = k
                    break

            j = i + 1
            while j < len(raw):
                stock = raw.iat[j, col_stock]
                if _is_blank(stock):
                    break

                sym = raw.iat[j, col_symbol] if col_symbol is not None else None
                qty = raw.iat[j, col_qty]
                sell_date = raw.iat[j, col_date]
                price = raw.iat[j, col_price]
                fees = raw.iat[j, col_fees] if col_fees is not None else 0.0

                add_transaction(
                    person=current_person,
                    symbol=str(sym).strip() if (col_symbol is not None and not _is_blank(sym)) else None,
                    stock_name=str(stock).strip(),
                    side="SELL",
                    trade_date=_to_date_str(sell_date),
                    quantity=float(qty),
                    price=float(price),
                    fees=float(fees) if pd.notna(fees) else 0.0
                )
                inserted += 1
                j += 1

            i = j
            continue

        i += 1

    return inserted

# ============================================================
# 5) Symbol backfill
#    If a transaction row only has Stock Name, we can map it to a symbol
#    using the holdings table (Stock Name -> symbol).
# ============================================================

def backfill_symbols_from_holdings():
    holdings = fetch_df("SELECT stock_name, symbol FROM holdings")
    if holdings.empty:
        return 0

    name_to_sym = {
        str(r["stock_name"]).strip(): str(r["symbol"]).strip().upper()
        for _, r in holdings.iterrows()
    }

    tx = fetch_df("""
        SELECT id, stock_name
        FROM transactions
        WHERE (symbol IS NULL OR TRIM(symbol) = '')
          AND stock_name IS NOT NULL AND TRIM(stock_name) <> ''
    """)

    updated = 0
    for _, r in tx.iterrows():
        tx_id = int(r["id"])
        nm = str(r["stock_name"]).strip()
        if nm in name_to_sym:
            execute("UPDATE transactions SET symbol = ? WHERE id = ?", (name_to_sym[nm], tx_id))
            updated += 1

    return updated

# ============================================================
# 6) Pricing (USD)
#    We use yfinance to pull latest close and show a 1-month trend.
# ============================================================

@st.cache_data(ttl=60 * 30)
def latest_close_usd(symbol: str) -> float:
    """Latest close price in USD for the given ticker symbol."""
    try:
        h = yf.Ticker(symbol).history(period="1d")
        if not h.empty and "Close" in h:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0

# ============================================================
# 7) Streamlit UI
#    Workflow:
#      1) Import holdings
#      2) Import transactions
#      3) Backfill symbols (if needed)
#      4) Rebuild FIFO
#      5) Explore portfolio + P/L tables
# ============================================================

st.subheader("Import Excel Files")

col1, col2 = st.columns(2)

with col1:
    st.markdown("Holdings Excel (Stock Name / symbol / Quantity(sum) / Account 1..)")
    holdings_file = st.file_uploader("Upload holdings file (.xlsx)", type=["xlsx"], key="holdings")
    holdings_sheet = st.text_input("Holdings sheet name", value="details")

    if st.button("Import Holdings"):
        if not holdings_file:
            st.warning("Upload the holdings Excel file first.")
        else:
            try:
                n = import_holdings_from_excel(holdings_file, sheet_name=holdings_sheet)
                st.success(f"Imported/updated {n} holdings rows.")
            except Exception as e:
                st.error(str(e))

with col2:
    st.markdown("Transactions Excel (Trader blocks + Buy table + Sell table)")
    tx_file = st.file_uploader("Upload transactions file (.xlsx)", type=["xlsx"], key="tx")
    tx_sheet = st.text_input("Transactions sheet name", value="Sheet2")

    if st.button("Import Buys and Sells"):
        if not tx_file:
            st.warning("Upload the transactions Excel file first.")
        else:
            try:
                n = import_transactions_from_excel(tx_file, sheet_name=tx_sheet)
                st.success(f"Imported {n} transactions.")
            except Exception as e:
                st.error(str(e))

st.markdown("---")

col3, col4, col5 = st.columns(3)

with col3:
    if st.button("Backfill missing symbols from holdings"):
        try:
            n = backfill_symbols_from_holdings()
            st.success(f"Filled symbol for {n} transactions.")
        except Exception as e:
            st.error(str(e))

with col4:
    if st.button("Rebuild FIFO lots and matches"):
        try:
            rebuild_fifo()
            st.success("Rebuilt FIFO lots and realized P/L tables.")
        except Exception as e:
            st.error(str(e))

with col5:
    if st.button("Backfill symbols and rebuild FIFO (recommended)"):
        try:
            n = backfill_symbols_from_holdings()
            rebuild_fifo()
            st.success(f"Filled symbol for {n} transactions and rebuilt FIFO tables.")
        except Exception as e:
            st.error(str(e))

# ============================================================
# 8) Views (what the app shows)
# ============================================================

st.subheader("Holdings (from Excel)")
holdings_df = fetch_df("""
    SELECT stock_name AS "Stock Name",
           symbol AS "Symbol",
           total_quantity AS "Total Quantity",
           account_quantities AS "Account Quantities",
           updated_at AS "Updated At"
    FROM holdings
    ORDER BY symbol
""")

if not holdings_df.empty:
    holdings_df["Account Quantities"] = holdings_df["Account Quantities"].apply(lambda x: json.loads(x))

st.dataframe(holdings_df, use_container_width=True)

st.subheader("Portfolio Value (USD)")

if not holdings_df.empty:
    rows = []
    total_usd = 0.0

    for _, r in holdings_df.iterrows():
        sym = str(r["Symbol"]).strip().upper()
        qty = float(r["Total Quantity"])

        # Pull a live price (latest close) and compute position value
        px = latest_close_usd(sym) if sym else 0.0
        amt = px * qty

        total_usd += amt
        rows.append({
            "Stock Name": r["Stock Name"],
            "Symbol": sym,
            "Quantity": qty,
            "Price (USD)": round(px, 2),
            "Amount (USD)": round(amt, 2)
        })

    port = pd.DataFrame(rows)

    # Weight is useful for basic portfolio analytics (feature engineering type metric)
    port["Weight (%)"] = port["Amount (USD)"].apply(
        lambda x: round((x / total_usd) * 100, 2) if total_usd > 0 else 0.0
    )

    st.dataframe(port.sort_values("Amount (USD)", ascending=False), use_container_width=True)
    st.write(f"Total Portfolio Value (USD): ${total_usd:,.2f}")

    st.subheader("Portfolio Distribution (USD)")
    chart_data = port.set_index("Stock Name")[["Amount (USD)"]]
    st.pyplot(
        chart_data.plot.pie(
            y="Amount (USD)",
            autopct="%.1f%%",
            figsize=(5, 5),
            legend=False,
            ylabel=""
        ).figure
    )
else:
    st.info("No holdings imported yet.")

st.subheader("Transactions Ledger")
tx = fetch_df("""
    SELECT id,
           person,
           COALESCE(symbol, '') AS symbol,
           COALESCE(stock_name, '') AS stock_name,
           side,
           trade_date,
           quantity,
           price,
           fees
    FROM transactions
    ORDER BY trade_date DESC, id DESC
""")
st.dataframe(tx, use_container_width=True)

st.subheader("Realized P/L Summary (USD)")
real_sum = fetch_df("""
    SELECT person,
           symbol,
           term,
           ROUND(SUM(realized_pl), 2) AS realized_pl_usd,
           ROUND(SUM(matched_qty), 4) AS matched_qty
    FROM matches
    GROUP BY person, symbol, term
    ORDER BY person, symbol, term
""")
st.dataframe(real_sum, use_container_width=True)

st.subheader("Realized P/L Details (FIFO matches)")
real_det = fetch_df("""
    SELECT sell_date,
           buy_date,
           person,
           symbol,
           ROUND(matched_qty, 4) AS matched_qty,
           ROUND(buy_price, 4) AS buy_price,
           ROUND(sell_price, 4) AS sell_price,
           term,
           ROUND(realized_pl, 2) AS realized_pl_usd
    FROM matches
    ORDER BY sell_date DESC, id DESC
""")
st.dataframe(real_det, use_container_width=True)

st.subheader("Price Trend Viewer (Last 1 Month)")
symbols = sorted(holdings_df["Symbol"].unique().tolist()) if not holdings_df.empty else []
sel = st.selectbox("Select a symbol", symbols)

if sel:
    try:
        h = yf.Ticker(sel).history(period="1mo")
        if not h.empty and "Close" in h:
            st.line_chart(h["Close"])
        else:
            st.warning("No price history returned.")
    except Exception as e:
        st.error(str(e))
