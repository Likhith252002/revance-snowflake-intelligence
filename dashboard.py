import streamlit as st
import snowflake.connector
import pandas as pd
import plotly.express as px
from config import SNOWFLAKE_CONFIG

st.set_page_config(page_title="Revance Practice Intelligence", page_icon="💉", layout="wide")

@st.cache_data
def load_data():
    conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
    cursor = conn.cursor()
    cursor.execute("USE WAREHOUSE COMPUTE_WH")

    cursor.execute("""
        SELECT p.PRACTICE_ID, p.PRACTICE_NAME, p.TERRITORY, p.PRACTICE_TYPE,
               p.MONTHS_ACTIVE, c.CHURN_RISK, c.RISK_LABEL
        FROM PRACTICES p
        JOIN CHURN_PREDICTIONS c ON p.PRACTICE_ID = c.PRACTICE_ID
    """)
    practices = pd.DataFrame(cursor.fetchall(),
        columns=["PRACTICE_ID","PRACTICE_NAME","TERRITORY",
                 "PRACTICE_TYPE","MONTHS_ACTIVE","CHURN_RISK","RISK_LABEL"])

    cursor.execute("""
        SELECT TERRITORY, PRODUCT, SUM(REVENUE) as TOTAL_REVENUE,
               COUNT(ORDER_ID) as TOTAL_ORDERS
        FROM ORDERS
        GROUP BY TERRITORY, PRODUCT
    """)
    orders = pd.DataFrame(cursor.fetchall(),
        columns=["TERRITORY","PRODUCT","TOTAL_REVENUE","TOTAL_ORDERS"])

    cursor.execute("""
        SELECT n.PRACTICE_ID, n.TERRITORY, n.REP_NOTES,
               a.SENTIMENT_SCORE, a.RISK_INSIGHT
        FROM REP_NOTES n
        JOIN REP_NOTES_ANALYSIS a ON n.PRACTICE_ID = a.PRACTICE_ID
    """)
    notes = pd.DataFrame(cursor.fetchall(),
        columns=["PRACTICE_ID","TERRITORY","REP_NOTES","SENTIMENT_SCORE","RISK_INSIGHT"])

    conn.close()
    return practices, orders, notes

practices, orders, notes = load_data()

# Header
st.title("💉 Revance Practice Intelligence Dashboard")
st.markdown("**AI-powered sales analytics | Churn Risk | Territory Health | Rep Insights**")
st.divider()

# KPI Cards
col1, col2, col3, col4 = st.columns(4)
high_risk = len(practices[practices["RISK_LABEL"] == "HIGH"])
med_risk = len(practices[practices["RISK_LABEL"] == "MEDIUM"])
low_risk = len(practices[practices["RISK_LABEL"] == "LOW"])
total_rev = orders["TOTAL_REVENUE"].sum()

col1.metric("Total Practices", len(practices))
col2.metric("🔴 High Risk", high_risk, delta=f"{round(high_risk/len(practices)*100)}%")
col3.metric("🟡 Medium Risk", med_risk)
col4.metric("💰 Total Revenue", f"${total_rev:,.0f}")

st.divider()

# Row 2
col1, col2 = st.columns(2)

with col1:
    st.subheader("🗺️ Churn Risk by Territory")
    territory_risk = practices.groupby(["TERRITORY","RISK_LABEL"]).size().reset_index(name="COUNT")
    fig = px.bar(territory_risk, x="TERRITORY", y="COUNT", color="RISK_LABEL",
                 color_discrete_map={"HIGH":"#ef4444","MEDIUM":"#f59e0b","LOW":"#22c55e"})
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("💊 Revenue by Product")
    product_rev = orders.groupby("PRODUCT")["TOTAL_REVENUE"].sum().reset_index()
    fig2 = px.pie(product_rev, values="TOTAL_REVENUE", names="PRODUCT")
    st.plotly_chart(fig2, use_container_width=True)

# Row 3
col1, col2 = st.columns(2)

with col1:
    st.subheader("📊 Revenue by Territory")
    terr_rev = orders.groupby("TERRITORY")["TOTAL_REVENUE"].sum().reset_index()
    fig3 = px.bar(terr_rev, x="TERRITORY", y="TOTAL_REVENUE",
                  color="TOTAL_REVENUE", color_continuous_scale="Blues")
    st.plotly_chart(fig3, use_container_width=True)

with col2:
    st.subheader("😊 Sentiment by Territory")
    sent = notes.groupby("TERRITORY")["SENTIMENT_SCORE"].mean().reset_index()
    fig4 = px.bar(sent, x="TERRITORY", y="SENTIMENT_SCORE",
                  color="SENTIMENT_SCORE", color_continuous_scale="RdYlGn")
    st.plotly_chart(fig4, use_container_width=True)

st.divider()

# High Risk Practices Table
st.subheader("🚨 High Risk Practices — Immediate Action Required")
high_risk_df = practices[practices["RISK_LABEL"]=="HIGH"].merge(
    notes[["PRACTICE_ID","RISK_INSIGHT"]], on="PRACTICE_ID", how="left"
)[["PRACTICE_ID","PRACTICE_NAME","TERRITORY","PRACTICE_TYPE","CHURN_RISK","RISK_INSIGHT"]]
high_risk_df["CHURN_RISK"] = high_risk_df["CHURN_RISK"].apply(lambda x: f"{x:.1%}")
st.dataframe(high_risk_df, use_container_width=True)

st.divider()

# Territory filter
st.subheader("🔍 Practice Explorer")
selected_territory = st.selectbox("Filter by Territory", ["All"] + list(practices["TERRITORY"].unique()))
filtered = practices if selected_territory == "All" else practices[practices["TERRITORY"]==selected_territory]
st.dataframe(filtered[["PRACTICE_ID","PRACTICE_NAME","TERRITORY","PRACTICE_TYPE","CHURN_RISK","RISK_LABEL"]],
             use_container_width=True)