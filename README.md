# Stock Portfolio Analytics Dashboard

An interactive Streamlit dashboard for multi-trader portfolio tracking, FIFO realized P/L analysis, and real-time valuation in USD.

## Key Features

- **Excel Import** — ingests holdings and transaction history from structured Excel files
- **SQLite Ledger** — 4-table normalized schema for persistent, reproducible portfolio data
- **FIFO Realized P/L** — fee-adjusted matching across 5,000+ trades with short/long term classification
- **Live Pricing** — real-time portfolio valuation via Yahoo Finance API
- **Multi-Account Support** — tracks holdings across multiple trader accounts
- **Portfolio Visualization** — asset allocation pie chart and position breakdown

## Tech Stack

Python, Pandas, SQLite, Streamlit, yfinance

## Run Locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Sample Data

Sample Excel files are provided in the `sample_data/` folder to test the dashboard without real portfolio data:
- `sample_holdings.xlsx` — sample holdings across 2 accounts
- `sample_transactions.xlsx` — sample BUY/SELL transactions for 2 traders

## How It Works

1. Upload holdings Excel → stored in SQLite
2. Upload transactions Excel → stored in SQLite  
3. Backfill missing symbols from holdings
4. Rebuild FIFO lots and realized P/L
5. Explore portfolio value, allocation, and P/L breakdown
