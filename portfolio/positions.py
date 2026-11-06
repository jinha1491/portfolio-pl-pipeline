from portfolio.db import connect

# Net shares per person and symbol from every trade on or before :day.
# Closed positions (bought and fully sold) drop out through the HAVING clause.
POSITIONS_AS_OF = """
    SELECT TRIM(person) AS person,
           UPPER(TRIM(symbol)) AS symbol,
           SUM(CASE side WHEN 'BUY' THEN quantity ELSE -quantity END) AS quantity
    FROM transactions
    WHERE symbol IS NOT NULL AND TRIM(symbol) <> '' AND trade_date <= :day
    GROUP BY TRIM(person), UPPER(TRIM(symbol))
    HAVING SUM(CASE side WHEN 'BUY' THEN quantity ELSE -quantity END) > 1e-9
"""


def open_positions(day):
    c = connect()
    rows = c.execute(POSITIONS_AS_OF, {"day": day.isoformat()}).fetchall()
    c.close()
    return rows


def held_symbols(day):
    return sorted({symbol for _, symbol, _ in open_positions(day)})
