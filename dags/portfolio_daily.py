import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowSkipException

from portfolio import bigquery_sync, prices, quality, snowflake_sync
from portfolio.ingest import process_inbox
from portfolio.fifo import rebuild_fifo

ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = ROOT / "dbt"
# dbt lives in its own virtualenv so its dependencies can't conflict with Airflow's.
DBT_BIN = os.environ.get("DBT_BIN", str(ROOT / ".dbt-venv" / "bin" / "dbt"))


def run_dbt(target):
    result = subprocess.run(
        [DBT_BIN, "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
         "--target", target],
        capture_output=True,
        text=True,
        env={**os.environ, "PGGSSENCMODE": "disable"},
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError(f"dbt build on {target} failed, see the output above")


def run_day():
    """The trading day this run is responsible for, in New York time."""
    ctx = get_current_context()
    when = ctx["logical_date"] or ctx["dag_run"].run_after
    return when.in_timezone("America/New_York").date()


@dag(
    dag_id="portfolio_daily",
    schedule="30 17 * * 1-5",
    start_date=pendulum.datetime(2024, 1, 1, tz="America/New_York"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["portfolio"],
)
def portfolio_daily():

    @task(retries=0)
    def ingest_files():
        result = process_inbox()
        for name, kind, status, n in result["files"]:
            print(f"{kind}/{name}: {status} ({n} rows)")
        print(f"symbols backfilled: {result['symbols_backfilled']}")
        if result["failed"]:
            raise ValueError(f"Moved to data/failed/: {result['failed']}")
        return len(result["files"])

    @task
    def extract_prices():
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

    @task(retries=0)
    def dbt_build():
        run_dbt("dev")

    @task
    def sync_to_snowflake():
        if not snowflake_sync.enabled():
            raise AirflowSkipException("SNOWFLAKE_ACCOUNT not set, Snowflake is off")
        counts = snowflake_sync.sync_tables()
        for table, n in counts.items():
            print(f"{table}: {n} rows")
        return counts

    @task(retries=0)
    def dbt_build_snowflake():
        run_dbt("snowflake")

    @task
    def sync_to_bigquery():
        if not bigquery_sync.enabled():
            raise AirflowSkipException("BIGQUERY_PROJECT not set, BigQuery is off")
        counts = bigquery_sync.sync_tables()
        for table, n in counts.items():
            print(f"{table}: {n} rows")
        return counts

    @task(retries=0)
    def dbt_build_bigquery():
        run_dbt("bigquery")

    ingested = ingest_files()
    closes = extract_prices()
    prices_ok = validate_prices()
    lots = rebuild_lots()

    ingested >> closes
    load_prices(closes) >> prices_ok
    ingested >> lots
    checked = validate_outputs()
    [prices_ok, lots] >> snapshot() >> checked
    checked >> dbt_build()
    checked >> sync_to_snowflake() >> dbt_build_snowflake()
    checked >> sync_to_bigquery() >> dbt_build_bigquery()


portfolio_daily()
