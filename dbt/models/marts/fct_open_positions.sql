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

ranked_prices as (
    select
        symbol,
        price_date,
        close,
        row_number() over (partition by symbol order by price_date desc) as rn
    from {{ ref('stg_daily_prices') }}
),

latest_price as (
    select symbol, price_date, close
    from ranked_prices
    where rn = 1
)

select
    o.person,
    o.symbol,
    o.quantity,
    {{ round_to('o.cost_basis / o.quantity', 4) }} as avg_cost,
    {{ round_to('p.close', 2) }} as latest_close,
    p.price_date as priced_on,
    {{ round_to('o.quantity * p.close', 2) }} as market_value,
    {{ round_to('o.quantity * p.close - o.cost_basis', 2) }} as unrealized_pl
from open_lots o
left join latest_price p on p.symbol = o.symbol
