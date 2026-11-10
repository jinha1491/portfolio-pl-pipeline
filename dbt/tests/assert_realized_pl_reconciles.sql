-- The monthly rollup has to add back up to the raw matches, apart from rounding.
select
    (select sum(realized_pl) from {{ ref('fct_realized_pl') }}) as rolled_up,
    (select coalesce(sum(realized_pl), 0) from {{ ref('stg_matches') }}) as raw
where abs(
    (select coalesce(sum(realized_pl), 0) from {{ ref('fct_realized_pl') }})
    - (select coalesce(sum(realized_pl), 0) from {{ ref('stg_matches') }})
) > 0.01 * (select count(*) + 1 from {{ ref('fct_realized_pl') }})
