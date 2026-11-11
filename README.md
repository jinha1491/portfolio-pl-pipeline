# Portfolio P/L Pipeline

Tracks positions and P/L for multiple traders. An Airflow DAG loads trade files and daily closing prices into Postgres, rebuilds FIFO lots, and snapshots positions every weekday. dbt builds the analytics tables in Postgres and, optionally, in Snowflake and BigQuery. There's a Streamlit app on top.

## Pipeline

`portfolio_daily` runs at 5:30 PM ET, Monday to Friday.

~~~
ingest_files ──> extract_prices ──> load_prices ──> validate_prices ──┐
     │                                                                ├──> snapshot ──> validate_outputs
     └──────────────────────────> rebuild_lots ───────────────────────┘                         │
                                                                                                ├──> dbt_build
                                                                                                ├──> sync_to_snowflake ──> dbt_build_snowflake
                                                                                                └──> sync_to_bigquery ──> dbt_build_bigquery
~~~

| task | |
|---|---|
| `ingest_files` | load new Excel files from `data/inbox/`, skip files already seen (SHA-256), move unreadable ones to `data/failed/` |
| `extract_prices` | closes for every held symbol from Yahoo Finance; skips the run if SPY has no bar (market closed) |
| `load_prices` | upsert into `daily_prices` |
| `validate_prices` | fail if a held symbol has no price, warn on >50% daily moves |
| `rebuild_lots` | rebuild FIFO lots and realized P/L from the full ledger in one transaction |
| `snapshot` | positions as of the run date, valued at that day's close |
| `validate_outputs` | snapshot completeness, repeated trades, holdings vs. ledger |
| `dbt_build` | build and test the dbt models in Postgres |
| `sync_to_snowflake` | copy the raw tables dbt reads into Snowflake (full refresh, row counts checked); skipped if Snowflake isn't configured |
| `dbt_build_snowflake` | build and test the same models in Snowflake |
| `sync_to_bigquery` | copy the same raw tables into BigQuery with load jobs (full refresh, row counts checked); skipped if BigQuery isn't configured |
| `dbt_build_bigquery` | build and test the same models in BigQuery |

All tasks are safe to rerun for the same day. I backfilled a year of history (253 trading days) through Airflow.

## Tables

Raw, written by the pipeline: `transactions`, `holdings`, `lots`, `matches`, `daily_prices`, `portfolio_snapshots`, `ingested_files`.

dbt (`analytics` schema):

- `stg_*`: cleaned views over the raw tables
- `fct_daily_portfolio_value`: value per trader per day, with day-over-day change
- `fct_realized_pl`: realized P/L by trader, symbol, term, and month
- `fct_open_positions`: open positions with average cost and unrealized P/L

Besides the standard dbt tests, `assert_open_lots_match_ledger` compares holdings from the FIFO lots against holdings summed straight from the trades. If FIFO has a bug, they won't match.

## Running it

~~~bash
docker compose up -d        # Postgres 16

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

python3 -m venv .dbt-venv   # dbt gets its own env, its dependencies conflict with Airflow's
.dbt-venv/bin/pip install -r requirements-dbt.txt
~~~

Install [Airflow 3](https://airflow.apache.org/docs/apache-airflow/stable/installation/installing-from-pypi.html) into `.venv`, then:

~~~bash
source airflow_env.sh
airflow standalone          # localhost:8080
streamlit run app.py
~~~

Snowflake is optional. Run `snowflake/setup.sql` once in a worksheet (it creates a warehouse, database, role, and a key-pair service user), copy `snowflake_env.example.sh` to `snowflake_env.sh`, fill in your account, and `source` it before starting Airflow. Without it, the two Snowflake tasks skip.

BigQuery is optional too. Create a Google Cloud project (the free BigQuery sandbox works) and a service account with the BigQuery Data Editor and BigQuery Job User roles, download its JSON key, copy `bigquery_env.example.sh` to `bigquery_env.sh`, fill in your project and key path, and `source` it before starting Airflow. Without it, the two BigQuery tasks skip.

Sample files are in `sample_data/`. Drop them into `data/inbox/holdings/` and `data/inbox/transactions/`, or upload them in the app.

## Tests

~~~bash
python -m pytest -q
~~~

Runs against a separate `portfolio_test` database. CI runs pytest against a Postgres container, then loads sample data with fixed prices (`scripts/load_sample.py`) and runs `dbt build`.

## Notes

This started as a Streamlit app on SQLite. I moved it to Postgres with `scripts/sqlite_to_postgres.py`, which copies every table with its ids and compares row counts and column sums before committing.

After the move, every Airflow task died instantly with an empty log. On macOS, libpq tries Kerberos auth first, and that crashes in forked processes, which is how Airflow's LocalExecutor runs tasks. The tests never forked, so they passed. Connections now set `gssencmode=disable`.

Known gaps: Airflow runs in standalone mode, `yfinance` is unofficial and can break, prices and quantities are floats rather than `NUMERIC`, and the data is sample data for two traders.
