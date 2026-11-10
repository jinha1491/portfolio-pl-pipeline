select
    id as transaction_id,
    trim(person) as person,
    upper(trim(symbol)) as symbol,
    stock_name,
    side,
    trade_date,
    quantity,
    price,
    fees,
    case side when 'BUY' then quantity else -quantity end as signed_quantity
from {{ source('raw', 'transactions') }}
where symbol is not null and trim(symbol) <> ''
