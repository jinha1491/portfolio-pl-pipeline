"""
Copies the raw tables dbt reads from Postgres into Snowflake's RAW schema.

Postgres stays the system of record. Snowflake is only the analytics copy, so
every run replaces the tables in full. The data is small enough that a full
copy is simpler and safer than tracking which rows changed.
"""
import os

from portfolio.db import fetch_df

TABLES = ["transactions", "lots", "matches", "daily_prices", "portfolio_snapshots"]


def enabled():
    return bool(os.environ.get("SNOWFLAKE_ACCOUNT"))


def connect():
    import snowflake.connector

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key_file=os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]),
        role=os.environ.get("SNOWFLAKE_ROLE", "PORTFOLIO_ROLE"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "PORTFOLIO_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "PORTFOLIO"),
    )


def sync_tables():
    """Replace each RAW table with the current Postgres rows, then check the counts match."""
    from snowflake.connector.pandas_tools import write_pandas

    counts = {}
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("CREATE SCHEMA IF NOT EXISTS RAW")
        for table in TABLES:
            df = fetch_df(f"SELECT * FROM {table}")
            ok, _, _, _ = write_pandas(
                conn, df, table.upper(), schema="RAW",
                auto_create_table=True, overwrite=True,
                quote_identifiers=False, use_logical_type=True,
            )
            if not ok:
                raise RuntimeError(f"write_pandas failed for {table}")
            in_snowflake = cur.execute(f"SELECT COUNT(*) FROM RAW.{table.upper()}").fetchone()[0]
            if in_snowflake != len(df):
                raise RuntimeError(f"{table}: {len(df)} rows in Postgres, {in_snowflake} in Snowflake")
            counts[table] = in_snowflake
    return counts
