"""
Copies the raw tables dbt reads from Postgres into BigQuery's raw dataset.

Same approach as the Snowflake sync: Postgres stays the system of record, every
run replaces each table in full with a load job, and the row counts are checked
afterwards.
"""
import os

from portfolio.db import fetch_df
from portfolio.snowflake_sync import TABLES

DATASET = "raw"


def enabled():
    return bool(os.environ.get("BIGQUERY_PROJECT"))


def connect():
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        os.path.expanduser(os.environ["BIGQUERY_KEYFILE"])
    )
    return bigquery.Client(
        project=os.environ["BIGQUERY_PROJECT"],
        credentials=creds,
        location=os.environ.get("BIGQUERY_LOCATION", "US"),
    )


def sync_tables():
    """Replace each raw table with the current Postgres rows, then check the counts match."""
    from google.cloud import bigquery

    client = connect()
    client.create_dataset(f"{client.project}.{DATASET}", exists_ok=True)
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")

    counts = {}
    for table in TABLES:
        df = fetch_df(f"SELECT * FROM {table}")
        table_id = f"{client.project}.{DATASET}.{table}"
        client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
        in_bigquery = list(client.query(f"SELECT COUNT(*) FROM `{table_id}`").result())[0][0]
        if in_bigquery != len(df):
            raise RuntimeError(f"{table}: {len(df)} rows in Postgres, {in_bigquery} in BigQuery")
        counts[table] = in_bigquery
    return counts
