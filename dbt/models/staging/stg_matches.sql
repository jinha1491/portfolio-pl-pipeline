select
    id as match_id,
    sell_txn_id as sell_transaction_id,
    buy_lot_id,
    person,
    symbol,
    buy_date,
    sell_date,
    matched_qty,
    buy_price,
    sell_price,
    realized_pl,
    term
from {{ source('raw', 'matches') }}
