-- Current open positions from the FIFO lots, valued at each symbol's latest close.
with open_lots as (
    select
        person,
        symbol,
        sum(remaining_qty) as quantity,
        sum(remaining_qty * buy_price) as cost_basis
    from {{ ref('stg_lots') }}
    where remaining_qty > 1e-9
    group by person, symbol
),

latest_price as (
    select distinct on (symbol)
        symbol,
        price_date,
        close
    from {{ ref('stg_daily_prices') }}
    order by symbol, price_date desc
)

select
    o.person,
    o.symbol,
    o.quantity,
    round((o.cost_basis / o.quantity)::numeric, 4) as avg_cost,
    round(p.close::numeric, 2) as latest_close,
    p.price_date as priced_on,
    round((o.quantity * p.close)::numeric, 2) as market_value,
    round((o.quantity * p.close - o.cost_basis)::numeric, 2) as unrealized_pl
from open_lots o
left join latest_price p on p.symbol = o.symbol
