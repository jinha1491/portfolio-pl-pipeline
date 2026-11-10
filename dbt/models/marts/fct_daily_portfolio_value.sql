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
),

with_prev as (
    select
        snapshot_date,
        person,
        positions,
        market_value,
        lag(market_value) over (partition by person order by snapshot_date) as prev_value
    from daily
)

select
    snapshot_date,
    person,
    positions,
    {{ round_to('market_value', 2) }} as market_value,
    {{ round_to('market_value - prev_value', 2) }} as change_from_prev_day,
    {{ round_to('market_value / nullif(prev_value, 0) - 1', 6) }} as pct_change_from_prev_day
from with_prev
