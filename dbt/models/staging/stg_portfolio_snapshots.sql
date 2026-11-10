select
    snapshot_date,
    person,
    symbol,
    quantity,
    close,
    market_value
from {{ source('raw', 'portfolio_snapshots') }}
