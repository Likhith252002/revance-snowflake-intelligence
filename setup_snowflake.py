"""
Provision the Snowflake schema and load the raw practice / orders / rep-note data.

This script is the single ingest step for the pipeline. It runs once per dataset
refresh and leaves Snowflake as the source of truth for everything downstream —
the churn model, the rep-note sentiment analysis, and the dashboard all read
from these three tables (PRACTICES, ORDERS, REP_NOTES) rather than the CSVs.

Why CREATE OR REPLACE: the dataset is regenerated cleanly each run, so we want
the load to be idempotent. In production you'd swap to MERGE INTO or an
incremental COPY pattern so historical orders and notes are preserved.
"""

import snowflake.connector
import pandas as pd
from config import SNOWFLAKE_CONFIG

conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
cursor = conn.cursor()

print("Connected to Snowflake")
cursor.execute("USE WAREHOUSE COMPUTE_WH")

# --- PRACTICES ---------------------------------------------------------------
# The customer master. CHURNED is the historical label the churn model trains
# against; in a real deployment this would be sourced from CRM disposition.
cursor.execute("""
CREATE OR REPLACE TABLE PRACTICES (
    PRACTICE_ID VARCHAR,
    PRACTICE_NAME VARCHAR,
    TERRITORY VARCHAR,
    PRACTICE_TYPE VARCHAR,
    MONTHS_ACTIVE INT,
    CHURNED INT
)
""")
print("PRACTICES table created")

# --- ORDERS ------------------------------------------------------------------
# Transactional fact table. ORDER_DATE drives the recency features the model
# uses to flag practices that have gone quiet.
cursor.execute("""
CREATE OR REPLACE TABLE ORDERS (
    ORDER_ID VARCHAR,
    PRACTICE_ID VARCHAR,
    PRODUCT VARCHAR,
    ORDER_DATE DATE,
    QUANTITY INT,
    REVENUE FLOAT,
    TERRITORY VARCHAR
)
""")
print("ORDERS table created")

# --- REP_NOTES ---------------------------------------------------------------
# Free-text from field reps. This is the qualitative channel — pricing
# concerns, competitor mentions, and delivery complaints land here first.
cursor.execute("""
CREATE OR REPLACE TABLE REP_NOTES (
    NOTE_ID VARCHAR,
    PRACTICE_ID VARCHAR,
    TERRITORY VARCHAR,
    NOTE_DATE DATE,
    REP_NOTES VARCHAR
)
""")
print("REP_NOTES table created")


def upload_df(df, table_name):
    # write_pandas chunks the upload and uses Snowflake's PUT/COPY internally,
    # which is much faster than row-by-row INSERTs for the volumes we have.
    from snowflake.connector.pandas_tools import write_pandas
    success, nchunks, nrows, _ = write_pandas(conn, df, table_name)
    print(f"Uploaded {nrows} rows to {table_name}")


upload_df(pd.read_csv("practices.csv"), "PRACTICES")
upload_df(pd.read_csv("orders.csv"), "ORDERS")
upload_df(pd.read_csv("rep_notes.csv"), "REP_NOTES")

cursor.close()
conn.close()
print("All data loaded into Snowflake")
