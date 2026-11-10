select
    symbol,
    price_date,
    close
from {{ source('raw', 'daily_prices') }}
