-- One row per trader per trading day. The day-over-day change includes the effect
-- of trades, so it's a change in value, not a pure investment return.
with daily as (
    select
        snapshot_date,
        person,
        count(*) as positions,
        sum(market_value) as market_value
    from {{ ref('stg_portfolio_snapshots') }}
    group by snapshot_date, person
)

select
    snapshot_date,
    person,
    positions,
    round(market_value::numeric, 2) as market_value,
    round((market_value - lag(market_value) over w)::numeric, 2) as change_from_prev_day,
    round((market_value / nullif(lag(market_value) over w, 0) - 1)::numeric, 6) as pct_change_from_prev_day
from daily
window w as (partition by person order by snapshot_date)
