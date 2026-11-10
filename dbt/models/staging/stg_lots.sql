select
    id as lot_id,
    buy_txn_id as buy_transaction_id,
    person,
    symbol,
    buy_date,
    buy_price,
    remaining_qty
from {{ source('raw', 'lots') }}
