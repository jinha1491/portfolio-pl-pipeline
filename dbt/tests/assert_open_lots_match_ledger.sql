-- FIFO lots and the raw trade ledger are two independent ways to get current holdings.
-- If the FIFO rebuild is correct, they agree for every trader and symbol.
with from_lots as (
    select person, symbol, quantity
    from {{ ref('fct_open_positions') }}
),

from_ledger as (
    select person, symbol, sum(signed_quantity) as quantity
    from {{ ref('stg_transactions') }}
    group by person, symbol
    having sum(signed_quantity) > 1e-9
)

select
    coalesce(l.person, g.person) as person,
    coalesce(l.symbol, g.symbol) as symbol,
    l.quantity as lots_quantity,
    g.quantity as ledger_quantity
from from_lots l
full outer join from_ledger g on g.person = l.person and g.symbol = l.symbol
where abs(coalesce(l.quantity, 0) - coalesce(g.quantity, 0)) > 1e-9
