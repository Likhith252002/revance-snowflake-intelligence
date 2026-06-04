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
print("✅ Connected!")

# Pull rep notes
cursor.execute("SELECT NOTE_ID, PRACTICE_ID, TERRITORY, REP_NOTES FROM REP_NOTES")
rows = cursor.fetchall()
df = pd.DataFrame(rows, columns=["NOTE_ID", "PRACTICE_ID", "TERRITORY", "REP_NOTES"])

# Sentiment analysis using TextBlob (mimics Cortex SENTIMENT)
def get_sentiment(text):
    score = TextBlob(text).sentiment.polarity
    return round(score, 3)

def get_risk_insight(note):
    note_lower = note.lower()
    if any(w in note_lower for w in ["switch", "competitor", "cuts", "difficult", "no orders", "complained"]):
        return "HIGH RISK — Practice showing churn signals"
    elif any(w in note_lower for w in ["training", "delivery", "concerned"]):
        return "MEDIUM RISK — Practice has concerns needing follow-up"
    else:
        return "LOW RISK — Practice appears satisfied and engaged"

df["SENTIMENT_SCORE"] = df["REP_NOTES"].apply(get_sentiment)
df["RISK_INSIGHT"] = df["REP_NOTES"].apply(get_risk_insight)

print("✅ Sentiment & risk analysis complete!")

# Upload to Snowflake
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
print(f"✅ Uploaded {len(df)} analyzed notes to Snowflake!")

# Preview
print("\n📊 Sample Results:")
print("-" * 80)
for _, row in df.head(5).iterrows():
    print(f"Practice: {row['PRACTICE_ID']} | Sentiment: {row['SENTIMENT_SCORE']} | {row['RISK_INSIGHT']}")

conn.close()
print("\n🎉 Analysis complete!")