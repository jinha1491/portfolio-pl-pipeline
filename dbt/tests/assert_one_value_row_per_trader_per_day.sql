select snapshot_date, person, count(*) as n
from {{ ref('fct_daily_portfolio_value') }}
group by snapshot_date, person
having count(*) > 1
