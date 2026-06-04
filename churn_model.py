from snowflake.snowpark import Session
from snowflake.snowpark.functions import col, count, avg
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from config import SNOWFLAKE_CONFIG

# Connect Snowpark
session = Session.builder.configs(SNOWFLAKE_CONFIG).create()
session.sql("USE WAREHOUSE COMPUTE_WH").collect()
session.sql("USE DATABASE SNOWFLAKE_LEARNING_DB").collect()
session.sql("USE SCHEMA PUBLIC").collect()
print("✅ Snowpark Session connected!")

# Load tables
practices = session.table("PRACTICES").to_pandas()
orders = session.table("ORDERS").to_pandas()

# Feature engineering
order_features = orders.groupby("PRACTICE_ID").agg(
    TOTAL_ORDERS=("ORDER_ID", "count"),
    AVG_REVENUE=("REVENUE", "mean"),
    PRODUCT_VARIETY=("PRODUCT", "nunique")
).reset_index()

# Merge
df = practices.merge(order_features, on="PRACTICE_ID", how="left").fillna(0)
print(f"✅ Dataset ready: {len(df)} rows")

# Train model
feature_cols = ["MONTHS_ACTIVE", "TOTAL_ORDERS", "AVG_REVENUE", "PRODUCT_VARIETY"]
X = df[feature_cols]
y = df["CHURNED"]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)
print("✅ Model trained!")
print(classification_report(model.predict(X_test), y_test))

# Score all practices
df["CHURN_RISK"] = model.predict_proba(df[feature_cols])[:, 1].round(3)
df["RISK_LABEL"] = df["CHURN_RISK"].apply(
    lambda x: "HIGH" if x > 0.6 else ("MEDIUM" if x > 0.35 else "LOW")
)

# Upload predictions to Snowflake
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
print(f"✅ Uploaded {len(pred_df)} churn predictions to Snowflake!")

conn.close()
session.close()
print("🎉 Churn model complete!")