-- The monthly rollup has to add back up to the raw matches, apart from rounding.
with totals as (
    select
        (select coalesce(sum(realized_pl), 0) from {{ ref('fct_realized_pl') }}) as rolled_up,
        (select coalesce(sum(realized_pl), 0) from {{ ref('stg_matches') }}) as raw,
        (select count(*) from {{ ref('fct_realized_pl') }}) as n_rows
)

select *
from totals
where abs(rolled_up - raw) > 0.01 * (n_rows + 1)
