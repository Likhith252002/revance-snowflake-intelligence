import snowflake.connector
import pandas as pd
from config import SNOWFLAKE_CONFIG

conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
cursor = conn.cursor()

print("✅ Connected to Snowflake!")
cursor.execute("USE WAREHOUSE COMPUTE_WH")

# Create tables
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
print("✅ PRACTICES table created")

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
print("✅ ORDERS table created")

cursor.execute("""
CREATE OR REPLACE TABLE REP_NOTES (
    NOTE_ID VARCHAR,
    PRACTICE_ID VARCHAR,
    TERRITORY VARCHAR,
    NOTE_DATE DATE,
    REP_NOTES VARCHAR
)
""")
print("✅ REP_NOTES table created")

# Upload data
def upload_df(df, table_name):
    from snowflake.connector.pandas_tools import write_pandas
    success, nchunks, nrows, _ = write_pandas(conn, df, table_name)
    print(f"✅ Uploaded {nrows} rows to {table_name}")

upload_df(pd.read_csv("practices.csv"), "PRACTICES")
upload_df(pd.read_csv("orders.csv"), "ORDERS")
upload_df(pd.read_csv("rep_notes.csv"), "REP_NOTES")

cursor.close()
conn.close()
print("🎉 All data loaded into Snowflake!")