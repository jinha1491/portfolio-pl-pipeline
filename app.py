import streamlit as st
import pandas as pd
import yfinance as yf
import json

from portfolio.db import init_db, fetch_df
from portfolio.ingest import process_inbox, save_upload
from portfolio.fifo import rebuild_fifo

st.set_page_config(page_title="Portfolio (USD) + FIFO Realized P/L", layout="wide")
st.title("Portfolio Analytics (USD) with Excel Import, SQLite, FIFO Realized P/L")

init_db()
stats = fetch_df("""
SELECT
  (SELECT COUNT(*) FROM transactions) AS transactions,
  (SELECT COUNT(*) FROM holdings) AS holdings,
  (SELECT COUNT(*) FROM lots) AS lots,
  (SELECT COUNT(*) FROM matches) AS matches
""")

st.write("Database Stats:")
st.dataframe(stats)

# ============================================================
# 6) Pricing (USD)
#    Use yfinance to pull latest close and show a 1-month trend.
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

st.subheader("Upload Excel Files")
st.caption("Uploads are saved to data/inbox/ and ingested right away. A file that was already ingested is skipped.")

col1, col2 = st.columns(2)
with col1:
    holdings_file = st.file_uploader("Holdings file (.xlsx)", type=["xlsx"], key="holdings")
with col2:
    tx_file = st.file_uploader("Transactions file (.xlsx)", type=["xlsx"], key="tx")

if st.button("Ingest uploaded files"):
    uploads = [("holdings", holdings_file), ("transactions", tx_file)]
    uploads = [(kind, f) for kind, f in uploads if f is not None]
    if not uploads:
        st.warning("Upload at least one file first.")
    else:
        for kind, f in uploads:
            save_upload(kind, f.name, f.getvalue())
        result = process_inbox()
        for name, kind, status, n in result["files"]:
            if status == "loaded":
                st.success(f"{name}: loaded {n} {kind} rows")
            else:
                st.info(f"{name}: already ingested, skipped")
        for msg in result["failed"]:
            st.error(msg)
        try:
            rebuild_fifo()
        except ValueError as e:
            st.error(str(e))

if st.button("Rebuild FIFO lots and matches"):
    try:
        st.success(f"Rebuilt FIFO: {rebuild_fifo()}")
    except ValueError as e:
        st.error(str(e))

# ============================================================
# 8) Views (what the app shows)
# ============================================================

st.subheader("Portfolio Value Over Time (USD)")
history = fetch_df("""
    SELECT snapshot_date, person, SUM(market_value) AS value
    FROM portfolio_snapshots
    GROUP BY snapshot_date, person
    ORDER BY snapshot_date
""")
if history.empty:
    st.info("No snapshots yet. Run the Airflow pipeline or a backfill to build history.")
else:
    by_person = history.pivot(index="snapshot_date", columns="person", values="value").fillna(0)
    by_person["Total"] = by_person.sum(axis=1)
    by_person.index = pd.to_datetime(by_person.index)
    st.line_chart(by_person)
    st.caption(
        f"{len(by_person)} trading days, last snapshot {by_person.index[-1].date()}: "
        f"${by_person['Total'].iloc[-1]:,.2f}"
    )

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
    holdings_df["Account Quantities"] = holdings_df["Account Quantities"].apply(
        lambda x: x if isinstance(x, list) else json.loads(x))

st.dataframe(holdings_df, width="stretch")

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

    # Weight is useful for basic portfolio analytics 
    port["Weight (%)"] = port["Amount (USD)"].apply(
        lambda x: round((x / total_usd) * 100, 2) if total_usd > 0 else 0.0
    )

    st.dataframe(port.sort_values("Amount (USD)", ascending=False), width="stretch")
    st.write(f"Total Portfolio Value (USD): ${total_usd:,.2f}")

    st.subheader("Portfolio Distribution (USD)")
    if total_usd > 0:
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
        st.warning("Live prices unavailable right now, so the distribution chart is hidden.")
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
st.dataframe(tx, width="stretch")

st.subheader("Realized P/L Summary (USD)")
real_sum = fetch_df("""
    SELECT person,
           symbol,
           term,
           ROUND(SUM(realized_pl)::numeric, 2) AS realized_pl_usd,
           ROUND(SUM(matched_qty)::numeric, 4) AS matched_qty
    FROM matches
    GROUP BY person, symbol, term
    ORDER BY person, symbol, term
""")
st.dataframe(real_sum, width="stretch")

st.subheader("Realized P/L Details (FIFO matches)")
real_det = fetch_df("""
    SELECT sell_date,
           buy_date,
           person,
           symbol,
           ROUND(matched_qty::numeric, 4) AS matched_qty,
           ROUND(buy_price::numeric, 4) AS buy_price,
           ROUND(sell_price::numeric, 4) AS sell_price,
           term,
           ROUND(realized_pl::numeric, 2) AS realized_pl_usd
    FROM matches
    ORDER BY sell_date DESC, id DESC
""")
st.dataframe(real_det, width="stretch")

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
