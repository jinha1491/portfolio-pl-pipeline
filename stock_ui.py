import streamlit as st
import pandas as pd
import os
import datetime
import yfinance as yf
import requests

st.set_page_config(page_title="Stock Entry", layout="wide")
st.title("📈 Stock Portfolio Input with Accounts")

CSV_FILE = "stocks.csv"

# If file doesn't exist, create it with headers
if not os.path.exists(CSV_FILE):
    df = pd.DataFrame(columns=["Stock Name", "Symbol", "Total Quantity", "Account Quantities"])
    df.to_csv(CSV_FILE, index=False)

# Step 1: Input fields
stock_name = st.text_input("Stock Name (e.g., Apple)")
symbol = st.text_input("Symbol (e.g., AAPL)")
total_qty = st.number_input("Total Quantity", min_value=0, step=1)

# Step 2: Account-related inputs
account_count = st.number_input("Number of Accounts", min_value=1, step=1, value=1)
st.markdown("#### Enter Quantity per Account:")

account_quantities = []
for i in range(account_count):
    qty = st.number_input(f"Account {i+1} Quantity", min_value=0, step=1, key=f"account_{i}")
    account_quantities.append(qty)

# Step 3: Save Button
if st.button("Save"):
    if not stock_name or not symbol:
        st.warning("Please fill in both Stock Name and Symbol.")
    else:
        total_from_accounts = sum(account_quantities)

        if total_from_accounts != total_qty:
            st.error(f"⚠️ Total quantity mismatch: {total_from_accounts} from accounts ≠ {total_qty}")
        else:
            df = pd.read_csv(CSV_FILE)

            new_row = {
                "Stock Name": stock_name,
                "Symbol": symbol,
                "Total Quantity": total_qty,
                "Account Quantities": account_quantities
            }

            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            df.to_csv(CSV_FILE, index=False)
            st.success("✅ Saved successfully!")

# Step 4: Show all saved data
st.subheader("📋 All Entered Stocks")
df = pd.read_csv(CSV_FILE)
st.dataframe(df)


st.subheader("📈 Stock Price Trend Viewer")

# 심볼 선택 드롭다운
df = pd.read_csv(CSV_FILE)
symbols = df["Symbol"].unique()

selected_symbol = st.selectbox("📌 Choose a stock symbol to view its daily trend:", symbols)

if selected_symbol:
    # yfinance에서 데이터 불러오기
    ticker = yf.Ticker(selected_symbol)
    hist = ticker.history(period="1mo")  # 최근 1개월 데이터
    close_prices = hist["Close"]

    # 시각화
    st.line_chart(close_prices)

    st.markdown(f"""
    - Showing last {len(close_prices)} days for **{selected_symbol}**
    - Most recent price: **${close_prices.iloc[-1]:,.2f}**
    """)
# Step 5: Currency Conversion (USD to KRW)
st.subheader("💱 Currency Conversion: USD to KRW")

@st.cache_data
def get_usd_to_krw():
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        response = requests.get(url)
        data = response.json()
        if data["result"] == "success":
            return data["rates"]["KRW"]
        else:
            st.error(f"API 응답 실패: {data}")
            return None
    except Exception as e:
        st.error(f"환율 정보를 가져오는 데 실패했습니다: {e}")
        return None

exchange_rate = get_usd_to_krw()
if exchange_rate:
    st.success(f"💵 Current Exchange Rate: 1 USD = {exchange_rate:,.2f} KRW")

    # Step 6: Show converted stock values
    st.subheader("💹 Converted Stock Values")
    price_data = []

    for _, row in df.iterrows():
        try:
            ticker = yf.Ticker(row["Symbol"])
            hist = ticker.history(period="1d")
            price = hist["Close"].iloc[-1]
        except:
            price = 0

        quantity = row["Total Quantity"]
        amount_usd = price * quantity
        amount_krw = amount_usd * exchange_rate

        price_data.append({
            "Stock Name": row["Stock Name"],
            "Symbol": row["Symbol"],
            "Price (USD)": round(price, 2),
            "Quantity": quantity,
            "Amount (USD)": round(amount_usd, 2),
            "Amount (KRW)": round(amount_krw)
        })

    result_df = pd.DataFrame(price_data)
    st.dataframe(result_df)

    # Step 7: Portfolio Pie Chart
st.subheader("🥧 Stock Portfolio Distribution (by USD Value)")

if not result_df.empty:
    chart_data = result_df[["Stock Name", "Amount (USD)"]]
    chart_data = chart_data.set_index("Stock Name")

    st.write("💡 Based on today's prices in USD:")
    st.pyplot(chart_data.plot.pie(
        y="Amount (USD)",
        autopct="%.1f%%",
        figsize=(5, 5),
        legend=False,
        ylabel=""
    ).figure)
else:
    st.info("포트폴리오 데이터가 없습니다. 상단에서 종목을 먼저 입력해주세요.")

    st.subheader("🗑️ Delete Stock Entry")

df = pd.read_csv(CSV_FILE)
symbols = df["Symbol"].unique()

delete_symbol = st.selectbox("삭제할 주식 선택:", symbols, index=0)

if st.button("삭제하기"):
    df = df[df["Symbol"] != delete_symbol]
    df.to_csv(CSV_FILE, index=False)
    st.success(f"✅ {delete_symbol} 삭제 완료! 페이지를 새로고침 해주세요.")

    

st.subheader("📌 과거 특정 날짜 시세를 표 형태로 확인하기")

start_date = st.date_input("Start Date", datetime.date(2025, 1, 1), key="start_date_picker")
end_date = st.date_input("End Date", datetime.date(2025, 5, 31), key="end_date_picker")

if selected_symbol:
    ticker = yf.Ticker(selected_symbol)
    try:
        hist_range = ticker.history(start=start_date, end=end_date)
        hist_range = hist_range.reset_index()
        hist_range["Date"] = hist_range["Date"].dt.date  # 날짜만 표시
        hist_table = hist_range[["Date", "Close"]]
        st.write(f"📅 Showing prices for **{selected_symbol}** from **{start_date}** to **{end_date}**")
        st.dataframe(hist_table)
    except Exception as e:
        st.error(f"❌ 데이터를 불러오는 데 실패했습니다: {e}")
        