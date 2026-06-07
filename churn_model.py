"""
Train and score the practice-level churn model.

Business question this answers: "Which practices in our 200-account book are
likely to stop ordering DAXXIFY, RHA, or SkinPen in the next quarter?"

Approach: pull practice tenure and aggregated order behaviour from Snowflake via
Snowpark, train a Random Forest classifier against the historical CHURNED label,
then score every active practice and write the probabilities back to Snowflake
as CHURN_PREDICTIONS. The dashboard and the rep-facing action queue both read
from that table.

Why Random Forest: it handles the mix of tenure, count, and revenue features
without scaling, gives an interpretable probability, and the 200-practice
dataset is small enough that boosting / deep models would add complexity
without measurable lift.
"""

from snowflake.snowpark import Session
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from config import SNOWFLAKE_CONFIG

# Snowpark gives us a pandas-like API directly on Snowflake tables — no manual
# JDBC fetch, no CSV staging. Important when this grows past 200 practices.
session = Session.builder.configs(SNOWFLAKE_CONFIG).create()
session.sql("USE WAREHOUSE COMPUTE_WH").collect()
session.sql("USE DATABASE SNOWFLAKE_LEARNING_DB").collect()
session.sql("USE SCHEMA PUBLIC").collect()
print("Snowpark Session connected")

practices = session.table("PRACTICES").to_pandas()
orders = session.table("ORDERS").to_pandas()

# --- Feature engineering ----------------------------------------------------
# These three aggregates are the practical signals a rep already uses to gauge
# account health: how often they order, how big each order is, and whether
# they buy across the product line or anchor on a single SKU.
order_features = orders.groupby("PRACTICE_ID").agg(
    TOTAL_ORDERS=("ORDER_ID", "count"),
    AVG_REVENUE=("REVENUE", "mean"),
    PRODUCT_VARIETY=("PRODUCT", "nunique")   # cross-sell breadth — single-product practices churn more
).reset_index()

# Left join so practices with zero recorded orders still get scored (they fill
# to zero, which is itself a strong churn signal).
df = practices.merge(order_features, on="PRACTICE_ID", how="left").fillna(0)
print(f"Dataset ready: {len(df)} rows")

# --- Train ------------------------------------------------------------------
# MONTHS_ACTIVE captures tenure — newer practices churn more often, longer-
# tenured practices have crossed the stickiness threshold.
feature_cols = ["MONTHS_ACTIVE", "TOTAL_ORDERS", "AVG_REVENUE", "PRODUCT_VARIETY"]
X = df[feature_cols]
y = df["CHURNED"]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
print("Model trained")
print(classification_report(model.predict(X_test), y_test))

# --- Score every practice ---------------------------------------------------
# We score the whole book (not just the test set) because the output is meant
# to drive a rep action queue, not just evaluate the model.
df["CHURN_RISK"] = model.predict_proba(df[feature_cols])[:, 1].round(3)

# Bucket thresholds were picked so HIGH ≈ "call this week", MEDIUM ≈ "check in
# this month", LOW ≈ "no action needed". The dashboard uses these labels
# directly for color coding and prioritization.
df["RISK_LABEL"] = df["CHURN_RISK"].apply(
    lambda x: "HIGH" if x > 0.6 else ("MEDIUM" if x > 0.35 else "LOW")
)

# --- Persist ----------------------------------------------------------------
pred_df = df[["PRACTICE_ID", "CHURN_RISK", "RISK_LABEL"]]
conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
cursor = conn.cursor()
cursor.execute("USE WAREHOUSE COMPUTE_WH")
cursor.execute("""
    CREATE OR REPLACE TABLE CHURN_PREDICTIONS (
        PRACTICE_ID VARCHAR,
        CHURN_RISK FLOAT,
        RISK_LABEL VARCHAR
    )
""")
write_pandas(conn, pred_df, "CHURN_PREDICTIONS")
print(f"Uploaded {len(pred_df)} churn predictions to Snowflake")

conn.close()
session.close()
print("Churn model complete")
