from datetime import timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowSkipException

from portfolio import prices, quality
from portfolio.db import init_db
from portfolio.fifo import rebuild_fifo


def run_day():
    """The trading day this run is responsible for, in New York time."""
    ctx = get_current_context()
    when = ctx["logical_date"] or ctx["dag_run"].run_after
    return when.in_timezone("America/New_York").date()


@dag(
    dag_id="portfolio_daily",
    schedule="30 17 * * 1-5",
    start_date=pendulum.datetime(2026, 11, 2, tz="America/New_York"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["portfolio"],
)
def portfolio_daily():

    @task
    def extract_prices():
        init_db()
        day = run_day()
        closes = prices.extract_closes(day)
        if closes is None:
            raise AirflowSkipException(f"Market closed on {day}")
        return closes

    @task
    def load_prices(closes):
        return prices.load_closes(run_day(), closes)

    @task
    def validate_prices():
        quality.enforce(quality.price_checks(run_day()))

    @task
    def rebuild_lots():
        return rebuild_fifo()

    @task
    def snapshot():
        return prices.snapshot_portfolio(run_day())

    @task
    def validate_outputs():
        quality.enforce(quality.output_checks(run_day()))

    loaded = load_prices(extract_prices())
    loaded >> validate_prices() >> rebuild_lots() >> snapshot() >> validate_outputs()


portfolio_daily()
