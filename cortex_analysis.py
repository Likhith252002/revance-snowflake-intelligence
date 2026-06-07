"""
Analyze rep field notes for sentiment and risk signals.

Business question this answers: "What are reps hearing in the field that the
order data can't see yet?" Pricing pushback, competitor mentions, and delivery
complaints almost always show up in rep notes weeks before the order pattern
changes — this script extracts that early signal.

Approach: pull all REP_NOTES, score each with a sentiment polarity in [-1, 1],
and tag it with a rule-based risk insight ("Practice showing churn signals",
"Practice has concerns needing follow-up", "Practice appears satisfied"). The
sentiment scorer here is TextBlob, which is a deliberate stand-in for
SNOWFLAKE.CORTEX.SENTIMENT — swapping in the Cortex function is a one-line SQL
change once the workspace has Cortex entitlements.
"""

import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
import pandas as pd
from textblob import TextBlob
from config import SNOWFLAKE_CONFIG

conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
cursor = conn.cursor()
cursor.execute("USE WAREHOUSE COMPUTE_WH")
cursor.execute("USE DATABASE SNOWFLAKE_LEARNING_DB")
cursor.execute("USE SCHEMA PUBLIC")
print("Connected")

# Pull everything once — at 200 notes this is cheap. For a real CRM volume
# you'd want to filter to notes written since the last analysis run.
cursor.execute("SELECT NOTE_ID, PRACTICE_ID, TERRITORY, REP_NOTES FROM REP_NOTES")
rows = cursor.fetchall()
df = pd.DataFrame(rows, columns=["NOTE_ID", "PRACTICE_ID", "TERRITORY", "REP_NOTES"])


def get_sentiment(text):
    # Polarity scale: -1 (very negative) → 0 (neutral) → +1 (very positive).
    # We surface this directly in the dashboard so leadership can spot
    # territories where rep sentiment has rolled into the warning band.
    score = TextBlob(text).sentiment.polarity
    return round(score, 3)


def get_risk_insight(note):
    # Keyword rules are deliberately interpretable — when a practice shows
    # up on the dashboard's action queue, the rep needs to know exactly *why*
    # it's flagged so they can prep the call. A black-box classifier would
    # hide that reasoning.
    note_lower = note.lower()
    if any(w in note_lower for w in ["switch", "competitor", "cuts", "difficult", "no orders", "complained"]):
        # These keywords are the strongest leading indicators of churn:
        # competitor mention, budget cuts, and "no orders in N days".
        return "HIGH RISK — Practice showing churn signals"
    elif any(w in note_lower for w in ["training", "delivery", "concerned"]):
        # Concerns that the rep can resolve directly — schedule training,
        # escalate a delivery issue. Not churn yet, but will be if ignored.
        return "MEDIUM RISK — Practice has concerns needing follow-up"
    else:
        return "LOW RISK — Practice appears satisfied and engaged"


df["SENTIMENT_SCORE"] = df["REP_NOTES"].apply(get_sentiment)
df["RISK_INSIGHT"] = df["REP_NOTES"].apply(get_risk_insight)

print("Sentiment & risk analysis complete")

# Persist to Snowflake so the dashboard can join it back against PRACTICES and
# CHURN_PREDICTIONS for the action queue. Replacing the table keeps the run
# idempotent.
cursor.execute("""
    CREATE OR REPLACE TABLE REP_NOTES_ANALYSIS (
        NOTE_ID VARCHAR,
        PRACTICE_ID VARCHAR,
        TERRITORY VARCHAR,
        REP_NOTES VARCHAR,
        SENTIMENT_SCORE FLOAT,
        RISK_INSIGHT VARCHAR
    )
""")
write_pandas(conn, df, "REP_NOTES_ANALYSIS")
print(f"Uploaded {len(df)} analyzed notes to Snowflake")

print("\nSample Results:")
print("-" * 80)
for _, row in df.head(5).iterrows():
    print(f"Practice: {row['PRACTICE_ID']} | Sentiment: {row['SENTIMENT_SCORE']} | {row['RISK_INSIGHT']}")

conn.close()
print("\nAnalysis complete")
