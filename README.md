# Stock Portfolio Analytics Dashboard

An interactive Streamlit dashboard for portfolio tracking and analysis.  
The app imports holdings and transaction history from Excel, stores data in SQLite, and computes FIFO-based realized profit/loss in USD.

## Key Features
- Excel-based portfolio and transaction ingestion
- SQLite-backed persistent storage
- FIFO realized P/L calculation (short-term vs long-term)
- Live price retrieval using Yahoo Finance
- Portfolio value and allocation visualization
- Multi-account holdings support

## Tech Stack
- Python
- Streamlit
- Pandas
- SQLite
- yfinance

## How to Run Locally
```bash
pip install -r requirements.txt
streamlit run app.py
