-- Realized P/L from the FIFO matches, by trader, symbol, holding term, and month sold.
select
    person,
    symbol,
    term,
    date_trunc('month', sell_date)::date as sell_month,
    count(*) as lots_matched,
    round(sum(matched_qty)::numeric, 4) as shares_sold,
    round(sum(realized_pl)::numeric, 2) as realized_pl
from {{ ref('stg_matches') }}
group by person, symbol, term, date_trunc('month', sell_date)
