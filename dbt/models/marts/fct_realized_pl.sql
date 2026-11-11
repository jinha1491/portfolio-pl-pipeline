-- Realized P/L from the FIFO matches, by trader, symbol, holding term, and month sold.
select
    person,
    symbol,
    term,
    cast({{ dbt.date_trunc('month', 'sell_date') }} as date) as sell_month,
    count(*) as lots_matched,
    {{ round_to('sum(matched_qty)', 4) }} as shares_sold,
    {{ round_to('sum(realized_pl)', 2) }} as realized_pl
from {{ ref('stg_matches') }}
group by person, symbol, term, sell_month
