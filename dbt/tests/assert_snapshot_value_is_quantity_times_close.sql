select *
from {{ ref('stg_portfolio_snapshots') }}
where abs(market_value - quantity * close) > 1e-6
