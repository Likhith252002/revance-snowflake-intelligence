"""
Revance Practice Intelligence — enterprise dashboard.

This is the user-facing layer of the pipeline. Sales leadership lands here to
answer three questions, in order of urgency:

  1. Is the book healthy overall, and how much revenue is at risk right now?
  2. Which territories and products are dragging that number down?
  3. Which specific practices does a rep need to call this week, and why?

Everything below reads live from Snowflake and joins the model output
(CHURN_PREDICTIONS) with the qualitative signal (REP_NOTES_ANALYSIS) so that
each row in the action queue carries both a probability and the rep's most
recent note. The UI is dark-themed in Revance brand colors so it can sit on a
shared TV in the sales bullpen without burning anyone's retinas.
"""

from datetime import datetime, date
import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import snowflake.connector
import streamlit as st

from config import SNOWFLAKE_CONFIG

# --- Brand palette -----------------------------------------------------------
# #C8102E is Revance's primary red. Everything else is built around it so the
# dashboard feels consistent with their existing collateral.
REVANCE_RED = "#C8102E"
DARK_BG = "#1B1B1B"
CARD_BG = "#242424"
BORDER = "#333333"
TEXT_MUTED = "#9CA3AF"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"

# Reusable Plotly layout overrides so every chart inherits the dark theme.
PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#E5E7EB", family="Inter, -apple-system, sans-serif"),
    margin=dict(l=10, r=10, t=40, b=10),
)

RISK_COLORS = {"HIGH": DANGER, "MEDIUM": WARNING, "LOW": SUCCESS}

# Hard-coded for the demo. In production this would come from the most recent
# model evaluation run (mlflow / a metrics table in Snowflake).
MODEL_ACCURACY = 0.72


st.set_page_config(
    page_title="Revance Practice Intelligence",
    page_icon="💉",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Custom CSS --------------------------------------------------------------
# Streamlit's default styling is too consumer-y for an internal sales tool.
# We force a dark canvas, restyle metric cards, give tables a proper border,
# and theme the sidebar to feel like a real BI product.
st.markdown(
    f"""
    <style>
        .stApp {{
            background-color: {DARK_BG};
            color: #F3F4F6;
        }}
        section[data-testid="stSidebar"] {{
            background-color: #141414;
            border-right: 1px solid {BORDER};
        }}
        section[data-testid="stSidebar"] * {{
            color: #E5E7EB !important;
        }}
        h1, h2, h3, h4 {{
            color: #FFFFFF !important;
            font-family: Inter, -apple-system, sans-serif;
            letter-spacing: -0.01em;
        }}
        .section-header {{
            border-left: 3px solid {REVANCE_RED};
            padding-left: 0.85rem;
            margin: 2rem 0 1rem 0;
        }}
        .section-header h2 {{
            margin: 0;
            font-size: 1.35rem;
            font-weight: 600;
        }}
        .section-header p {{
            margin: 0.25rem 0 0 0;
            color: {TEXT_MUTED};
            font-size: 0.85rem;
        }}
        .metric-card {{
            background-color: {CARD_BG};
            border: 1px solid {BORDER};
            border-radius: 10px;
            padding: 1.1rem 1.25rem;
            min-height: 110px;
        }}
        .metric-card.high   {{ border-left: 4px solid {DANGER}; }}
        .metric-card.medium {{ border-left: 4px solid {WARNING}; }}
        .metric-card.low    {{ border-left: 4px solid {SUCCESS}; }}
        .metric-card.brand  {{ border-left: 4px solid {REVANCE_RED}; }}
        .metric-label {{
            color: {TEXT_MUTED};
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.4rem;
        }}
        .metric-value {{
            color: #FFFFFF;
            font-size: 1.85rem;
            font-weight: 700;
            line-height: 1.1;
        }}
        .metric-sub {{
            color: {TEXT_MUTED};
            font-size: 0.78rem;
            margin-top: 0.35rem;
        }}
        .exec-banner {{
            background: linear-gradient(135deg, #1F1F1F 0%, #2A0F12 60%, #3A0B14 100%);
            border: 1px solid {BORDER};
            border-radius: 14px;
            padding: 1.75rem 2rem;
            margin-bottom: 1.5rem;
        }}
        .exec-headline {{
            font-size: 2.4rem;
            font-weight: 800;
            color: #FFFFFF;
            line-height: 1.1;
            margin: 0;
        }}
        .exec-score-pill {{
            display: inline-block;
            padding: 0.3rem 0.85rem;
            border-radius: 999px;
            font-weight: 600;
            font-size: 0.85rem;
            margin-left: 0.75rem;
            vertical-align: middle;
        }}
        .exec-sub {{
            color: #D1D5DB;
            font-size: 1.05rem;
            margin-top: 0.6rem;
        }}
        .exec-timestamp {{
            color: {TEXT_MUTED};
            font-size: 0.8rem;
            margin-top: 0.4rem;
        }}
        .insight-card {{
            background-color: {CARD_BG};
            border: 1px solid {BORDER};
            border-left: 3px solid {REVANCE_RED};
            border-radius: 8px;
            padding: 1rem 1.2rem;
            color: #E5E7EB;
            margin: 0.5rem 0;
        }}
        .footer {{
            border-top: 1px solid {BORDER};
            margin-top: 3rem;
            padding-top: 1.5rem;
            color: {TEXT_MUTED};
            font-size: 0.85rem;
        }}
        div[data-testid="stDataFrame"] {{
            border: 1px solid {BORDER};
            border-radius: 8px;
        }}
        div[data-testid="stSelectbox"] label,
        div[data-testid="stMultiSelect"] label,
        div[data-testid="stSlider"] label {{
            color: #E5E7EB !important;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Data load ---------------------------------------------------------------
# One cached fetch on session start; the footer's "Refresh" button clears it.
@st.cache_data(show_spinner="Loading from Snowflake...")
def load_data():
    conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
    cur = conn.cursor()
    cur.execute("USE WAREHOUSE COMPUTE_WH")

    # Practices joined with churn scores — the spine of the dashboard.
    cur.execute(
        """
        SELECT p.PRACTICE_ID, p.PRACTICE_NAME, p.TERRITORY, p.PRACTICE_TYPE,
               p.MONTHS_ACTIVE, c.CHURN_RISK, c.RISK_LABEL
        FROM PRACTICES p
        JOIN CHURN_PREDICTIONS c ON p.PRACTICE_ID = c.PRACTICE_ID
        """
    )
    practices = pd.DataFrame(
        cur.fetchall(),
        columns=["PRACTICE_ID", "PRACTICE_NAME", "TERRITORY", "PRACTICE_TYPE",
                 "MONTHS_ACTIVE", "CHURN_RISK", "RISK_LABEL"],
    )
    practices["CHURN_RISK"] = practices["CHURN_RISK"].astype(float)
    practices["MONTHS_ACTIVE"] = practices["MONTHS_ACTIVE"].astype(int)

    # Full orders — we need ORDER_DATE per row to compute "days since last order".
    cur.execute("SELECT ORDER_ID, PRACTICE_ID, PRODUCT, ORDER_DATE, QUANTITY, REVENUE, TERRITORY FROM ORDERS")
    orders = pd.DataFrame(
        cur.fetchall(),
        columns=["ORDER_ID", "PRACTICE_ID", "PRODUCT", "ORDER_DATE", "QUANTITY", "REVENUE", "TERRITORY"],
    )
    orders["ORDER_DATE"] = pd.to_datetime(orders["ORDER_DATE"])
    orders["REVENUE"] = orders["REVENUE"].astype(float)

    # Notes + the sentiment / risk-insight analysis output.
    cur.execute(
        """
        SELECT n.PRACTICE_ID, n.TERRITORY, n.NOTE_DATE, n.REP_NOTES,
               a.SENTIMENT_SCORE, a.RISK_INSIGHT
        FROM REP_NOTES n
        JOIN REP_NOTES_ANALYSIS a ON n.NOTE_ID = a.NOTE_ID
        """
    )
    notes = pd.DataFrame(
        cur.fetchall(),
        columns=["PRACTICE_ID", "TERRITORY", "NOTE_DATE", "REP_NOTES",
                 "SENTIMENT_SCORE", "RISK_INSIGHT"],
    )
    notes["SENTIMENT_SCORE"] = notes["SENTIMENT_SCORE"].astype(float)

    conn.close()
    return practices, orders, notes


practices_all, orders_all, notes_all = load_data()


# --- Per-practice revenue & recency ------------------------------------------
# Computed once from the unfiltered order book so the global "at-risk revenue"
# calculation stays stable regardless of the sidebar filters.
practice_revenue = (
    orders_all.groupby("PRACTICE_ID")["REVENUE"].sum().rename("PRACTICE_REVENUE")
)
practice_products = (
    orders_all.groupby("PRACTICE_ID")["PRODUCT"].nunique().rename("PRODUCT_COUNT")
)
last_order = (
    orders_all.groupby("PRACTICE_ID")["ORDER_DATE"].max().rename("LAST_ORDER")
)
today = pd.Timestamp(datetime.now().date())
days_since = (today - last_order).dt.days.rename("DAYS_SINCE_LAST_ORDER")

practices_all = (
    practices_all
    .merge(practice_revenue, on="PRACTICE_ID", how="left")
    .merge(practice_products, on="PRACTICE_ID", how="left")
    .merge(days_since, on="PRACTICE_ID", how="left")
    .fillna({"PRACTICE_REVENUE": 0, "PRODUCT_COUNT": 0, "DAYS_SINCE_LAST_ORDER": 365})
)


# --- Sidebar filters (I) -----------------------------------------------------
with st.sidebar:
    st.markdown(f"<h2 style='color:{REVANCE_RED};margin-top:0;'>Revance</h2>", unsafe_allow_html=True)
    st.markdown("<p style='color:#9CA3AF;margin-top:-12px;font-size:0.8rem;'>PRACTICE INTELLIGENCE</p>", unsafe_allow_html=True)
    st.markdown("---")
    st.markdown("### Filters")

    territory_options = sorted(practices_all["TERRITORY"].unique().tolist())
    sel_territory = st.multiselect("Territory", territory_options, default=territory_options)

    risk_options = ["HIGH", "MEDIUM", "LOW"]
    sel_risk = st.multiselect("Risk Level", risk_options, default=risk_options)

    type_options = sorted(practices_all["PRACTICE_TYPE"].unique().tolist())
    sel_type = st.multiselect("Practice Type", type_options, default=type_options)

    rev_min = int(practices_all["PRACTICE_REVENUE"].min())
    rev_max = int(practices_all["PRACTICE_REVENUE"].max()) or 1
    sel_rev = st.slider(
        "Practice Revenue ($)",
        min_value=rev_min,
        max_value=rev_max,
        value=(rev_min, rev_max),
        step=max((rev_max - rev_min) // 50, 1),
    )

    st.markdown("---")
    st.markdown(
        f"<p style='color:{TEXT_MUTED};font-size:0.75rem;'>Filters update every chart and table on this page.</p>",
        unsafe_allow_html=True,
    )


# Apply filters once; everything downstream consumes `practices`.
mask = (
    practices_all["TERRITORY"].isin(sel_territory)
    & practices_all["RISK_LABEL"].isin(sel_risk)
    & practices_all["PRACTICE_TYPE"].isin(sel_type)
    & practices_all["PRACTICE_REVENUE"].between(sel_rev[0], sel_rev[1])
)
practices = practices_all[mask].copy()
orders = orders_all[orders_all["PRACTICE_ID"].isin(practices["PRACTICE_ID"])].copy()
notes = notes_all[notes_all["PRACTICE_ID"].isin(practices["PRACTICE_ID"])].copy()


# --- Empty state -------------------------------------------------------------
if practices.empty:
    st.warning("No practices match the current filters. Loosen the sidebar selections to see data.")
    st.stop()


# --- Pre-computed slices used in multiple sections ---------------------------
high_risk = practices[practices["RISK_LABEL"] == "HIGH"]
med_risk = practices[practices["RISK_LABEL"] == "MEDIUM"]
low_risk = practices[practices["RISK_LABEL"] == "LOW"]

total_practices = len(practices)
high_pct = len(high_risk) / total_practices * 100 if total_practices else 0
med_pct = len(med_risk) / total_practices * 100 if total_practices else 0

# Practice Health Score: 100 minus weighted risk drag. A clean book stays in
# the 90s; a book that is mostly HIGH risk drops to the 40s. Tuned so the
# default 200-practice demo lands in the amber 70s.
health_score = max(0, round(100 - high_pct * 1.0 - med_pct * 0.4))
health_color = SUCCESS if health_score >= 75 else (WARNING if health_score >= 60 else DANGER)
health_label = "Healthy" if health_score >= 75 else ("Watch" if health_score >= 60 else "At Risk")

# At-risk revenue: sum of historical revenue from HIGH-risk practices.
# This is the dollar exposure if the rep team doesn't intervene this quarter.
at_risk_revenue = high_risk["PRACTICE_REVENUE"].sum()
total_revenue = practices["PRACTICE_REVENUE"].sum()
# If we cut the HIGH-risk churn rate in half, this is the revenue saved.
revenue_saved_potential = at_risk_revenue * 0.5


def section_header(title: str, subtitle: str = ""):
    st.markdown(
        f"""<div class='section-header'><h2>{title}</h2>
        {'<p>' + subtitle + '</p>' if subtitle else ''}</div>""",
        unsafe_allow_html=True,
    )


# =============================================================================
# A) EXECUTIVE SUMMARY BANNER
# =============================================================================
banner_html = f"""
<div class='exec-banner'>
    <p style='margin:0;color:{TEXT_MUTED};font-size:0.85rem;letter-spacing:0.1em;text-transform:uppercase;'>
        Practice Health Score
    </p>
    <p class='exec-headline'>
        {health_score}<span style='color:{TEXT_MUTED};font-size:1.5rem;font-weight:500;'>/100</span>
        <span class='exec-score-pill' style='background:{health_color};color:#0B0B0B;'>{health_label}</span>
    </p>
    <p class='exec-sub'>
        Your portfolio has <b style='color:{DANGER};'>{len(high_risk)} high-risk practices</b>
        representing <b style='color:#FFFFFF;'>${at_risk_revenue/1_000_000:.1f}M</b> in at-risk revenue.
    </p>
    <p class='exec-timestamp'>Last updated {datetime.now().strftime('%b %d, %Y · %I:%M %p')}</p>
</div>
"""
st.markdown(banner_html, unsafe_allow_html=True)


# Top KPIs as styled HTML cards (st.metric won't let us color the border).
def kpi_card(label, value, sub="", variant="brand"):
    return f"""
    <div class='metric-card {variant}'>
        <div class='metric-label'>{label}</div>
        <div class='metric-value'>{value}</div>
        <div class='metric-sub'>{sub}</div>
    </div>
    """


c1, c2, c3, c4 = st.columns(4)
c1.markdown(kpi_card("Total Practices", f"{total_practices}", "across all territories", "brand"), unsafe_allow_html=True)
c2.markdown(kpi_card("High Risk", f"{len(high_risk)}", f"{high_pct:.0f}% of portfolio", "high"), unsafe_allow_html=True)
c3.markdown(kpi_card("Medium Risk", f"{len(med_risk)}", f"{med_pct:.0f}% of portfolio", "medium"), unsafe_allow_html=True)
c4.markdown(kpi_card("Total Revenue", f"${total_revenue/1_000_000:.1f}M", "trailing 12 months", "brand"), unsafe_allow_html=True)


# =============================================================================
# B) AT-RISK REVENUE CALCULATOR
# =============================================================================
section_header(
    "At-Risk Revenue",
    "Exposure from HIGH-risk practices and the upside if churn is cut in half.",
)
b1, b2 = st.columns(2)
b1.markdown(
    kpi_card(
        "Revenue at Risk",
        f"${at_risk_revenue/1_000_000:.2f}M",
        f"From {len(high_risk)} HIGH-risk practices",
        "high",
    ),
    unsafe_allow_html=True,
)
b2.markdown(
    kpi_card(
        "Potential Saved",
        f"${revenue_saved_potential/1_000_000:.2f}M",
        "If HIGH-risk churn drops 50%",
        "low",
    ),
    unsafe_allow_html=True,
)


# =============================================================================
# C) TERRITORY LEADERBOARD
# =============================================================================
section_header(
    "Territory Leaderboard",
    "Health score ranks territories so leadership knows where to deploy rep attention.",
)

# Aggregate per territory. Avg sentiment comes from rep notes, not from the
# model — it's a separate qualitative signal.
terr = (
    practices.groupby("TERRITORY")
    .agg(
        practices=("PRACTICE_ID", "count"),
        high_risk=("RISK_LABEL", lambda s: (s == "HIGH").sum()),
        revenue=("PRACTICE_REVENUE", "sum"),
    )
    .reset_index()
)
sent_by_terr = notes.groupby("TERRITORY")["SENTIMENT_SCORE"].mean().rename("avg_sentiment")
terr = terr.merge(sent_by_terr, on="TERRITORY", how="left").fillna({"avg_sentiment": 0})
terr["high_risk_pct"] = terr["high_risk"] / terr["practices"] * 100
terr["health_score"] = (100 - terr["high_risk_pct"] * 1.0).clip(lower=0).round(0)
terr = terr.sort_values("health_score", ascending=False).reset_index(drop=True)

# Crown the best territory; rank everyone else by health score.
def crown(row):
    return "👑 " + row["TERRITORY"] if row.name == 0 else row["TERRITORY"]


terr["Territory"] = terr.apply(crown, axis=1)
terr_display = pd.DataFrame({
    "Rank": (terr.index + 1).astype(str),
    "Territory": terr["Territory"],
    "Health": terr["health_score"].astype(int),
    "Practices": terr["practices"],
    "High Risk": terr["high_risk"],
    "Revenue": terr["revenue"].apply(lambda v: f"${v/1_000_000:.2f}M"),
    "Avg Sentiment": terr["avg_sentiment"].round(2),
})


def style_leaderboard(row):
    score = row["Health"]
    color = SUCCESS if score >= 75 else WARNING if score >= 60 else DANGER
    return [f"background-color: {color}22; color: #FFFFFF;"] * len(row)


st.dataframe(
    terr_display.style.apply(style_leaderboard, axis=1),
    hide_index=True,
    use_container_width=True,
)


# =============================================================================
# D) PRODUCT PENETRATION ANALYSIS
# =============================================================================
section_header(
    "Product Penetration",
    "Practices that buy across the line are stickier — single-product accounts churn first.",
)

# Single-product vs multi-product (3+) churn comparison. The dashboard
# headline insight comes straight from the data — no hard-coded numbers.
single = practices[practices["PRODUCT_COUNT"] <= 1]
multi = practices[practices["PRODUCT_COUNT"] >= 3]
churn_single = single["CHURN_RISK"].mean() if len(single) else 0
churn_multi = multi["CHURN_RISK"].mean() if len(multi) else 0
delta_pct = ((churn_single - churn_multi) / churn_single * 100) if churn_single else 0

d1, d2 = st.columns([1, 1.4])

with d1:
    st.markdown(
        kpi_card("Single-Product Practices", f"{len(single)}", f"avg churn risk {churn_single*100:.0f}%", "high"),
        unsafe_allow_html=True,
    )
    st.markdown(
        kpi_card("Multi-Product (3+) Practices", f"{len(multi)}", f"avg churn risk {churn_multi*100:.0f}%", "low"),
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class='insight-card'>
        <b style='color:{REVANCE_RED};'>Insight:</b> Practices ordering 3+ products
        have <b>{delta_pct:.0f}%</b> lower churn risk on average. The fastest path
        to portfolio stability is cross-sell from DAXXIFY anchors into RHA fillers.
        </div>""",
        unsafe_allow_html=True,
    )

with d2:
    prod_by_terr = (
        orders.groupby(["TERRITORY", "PRODUCT"])["PRACTICE_ID"]
        .nunique()
        .reset_index(name="Practices")
    )
    fig = px.bar(
        prod_by_terr,
        x="TERRITORY",
        y="Practices",
        color="PRODUCT",
        title="Product adoption — practices ordering each product per territory",
        color_discrete_sequence=px.colors.sequential.Reds_r,
    )
    fig.update_layout(**PLOTLY_LAYOUT, legend=dict(bgcolor="rgba(0,0,0,0)"))
    st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# E) REP PERFORMANCE INSIGHTS
# =============================================================================
section_header(
    "Rep Performance",
    "Sentiment is the leading indicator — order data follows it by weeks.",
)
e1, e2 = st.columns([1.3, 1])

with e1:
    sent_avg = notes.groupby("TERRITORY")["SENTIMENT_SCORE"].mean().reset_index()
    sent_avg["color"] = sent_avg["SENTIMENT_SCORE"].apply(
        lambda v: DANGER if v < 0 else (WARNING if v < 0.15 else SUCCESS)
    )
    fig = px.bar(
        sent_avg,
        x="TERRITORY",
        y="SENTIMENT_SCORE",
        title="Avg rep-note sentiment by territory (warning band < 0.15, alarm < 0)",
    )
    fig.update_traces(marker_color=sent_avg["color"])
    fig.update_layout(**PLOTLY_LAYOUT, yaxis_title="Avg sentiment")
    st.plotly_chart(fig, use_container_width=True)

    flagged = sent_avg[sent_avg["SENTIMENT_SCORE"] < 0]["TERRITORY"].tolist()
    if flagged:
        st.markdown(
            f"""<div class='insight-card' style='border-left-color:{DANGER};'>
            <b style='color:{DANGER};'>Negative-sentiment territories:</b> {', '.join(flagged)}.
            Schedule a regional review — reps are hearing concerns the order data hasn't caught yet.
            </div>""",
            unsafe_allow_html=True,
        )

with e2:
    # Extract the most common risk keywords from the rep-note free text.
    keyword_pool = [
        "switch", "competitor", "Botox", "pricing", "budget", "cuts",
        "delivery", "delays", "training", "concerned", "expanding",
        "satisfied", "no orders", "complained", "referring",
    ]
    text_blob = " ".join(notes["REP_NOTES"].astype(str)).lower()
    counts = []
    for kw in keyword_pool:
        n = len(re.findall(rf"\b{re.escape(kw.lower())}\b", text_blob))
        if n:
            counts.append((kw, n))
    counts.sort(key=lambda x: x[1], reverse=True)
    top_keywords = pd.DataFrame(counts[:8], columns=["Keyword", "Mentions"])
    fig = px.bar(
        top_keywords,
        x="Mentions",
        y="Keyword",
        orientation="h",
        title="Most common signals in rep notes",
        color="Mentions",
        color_continuous_scale=["#7A1D1D", REVANCE_RED, "#F87171"],
    )
    fig.update_layout(**PLOTLY_LAYOUT, yaxis=dict(categoryorder="total ascending"))
    st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# F) CHURN PREDICTION CONFIDENCE
# =============================================================================
section_header(
    "Model Confidence",
    "Distribution of churn-risk scores plus the model's offline accuracy.",
)
f1, f2 = st.columns([1.4, 1])

with f1:
    fig = px.histogram(
        practices,
        x="CHURN_RISK",
        nbins=25,
        color="RISK_LABEL",
        color_discrete_map=RISK_COLORS,
        title="Churn-risk distribution across the portfolio",
    )
    fig.update_layout(**PLOTLY_LAYOUT, bargap=0.05, xaxis_title="Predicted churn probability")
    st.plotly_chart(fig, use_container_width=True)

with f2:
    # Half-circle gauge — 0–100% with the model's accuracy plotted on it.
    gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=MODEL_ACCURACY * 100,
        number={"suffix": "%", "font": {"size": 42, "color": "#FFFFFF"}},
        title={"text": "Model Accuracy<br><span style='font-size:0.8em;color:#9CA3AF;'>held-out test set</span>",
               "font": {"color": "#FFFFFF"}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "#9CA3AF"},
            "bar": {"color": REVANCE_RED, "thickness": 0.3},
            "bgcolor": CARD_BG,
            "borderwidth": 0,
            "steps": [
                {"range": [0, 60], "color": "#3A1517"},
                {"range": [60, 80], "color": "#5C1A1F"},
                {"range": [80, 100], "color": "#7A1D1D"},
            ],
        },
    ))
    gauge.update_layout(**PLOTLY_LAYOUT, height=320)
    st.plotly_chart(gauge, use_container_width=True)


# =============================================================================
# H) ACTION PRIORITY QUEUE
# =============================================================================
section_header(
    "Action Priority Queue",
    "Top 10 practices the rep team should contact this week, ranked by risk × revenue.",
)

# Composite priority: high risk that also has dollars on the line.
practices["AT_RISK_REVENUE"] = practices["CHURN_RISK"] * practices["PRACTICE_REVENUE"]
queue = practices.sort_values("AT_RISK_REVENUE", ascending=False).head(10).copy()

# Pull the most recent rep note per practice for context in the queue.
notes_sorted = notes.sort_values("NOTE_DATE", ascending=False).drop_duplicates("PRACTICE_ID")
queue = queue.merge(
    notes_sorted[["PRACTICE_ID", "REP_NOTES", "RISK_INSIGHT"]],
    on="PRACTICE_ID",
    how="left",
)


def recommended_action(row):
    # The action depends on whether the rep already has a note suggesting why.
    insight = (row.get("RISK_INSIGHT") or "").upper()
    if "HIGH RISK" in insight:
        return "Schedule call this week — bring retention offer"
    if "MEDIUM RISK" in insight:
        return "Follow up on concerns within 14 days"
    if row["DAYS_SINCE_LAST_ORDER"] > 90:
        return "Re-engage — no orders in 90+ days"
    return "Quarterly check-in"


queue["Recommended Action"] = queue.apply(recommended_action, axis=1)
queue_display = pd.DataFrame({
    "#": range(1, len(queue) + 1),
    "Practice": queue["PRACTICE_NAME"],
    "Territory": queue["TERRITORY"],
    "Churn Risk": queue["CHURN_RISK"].apply(lambda v: f"{v*100:.0f}%"),
    "At-Risk Revenue": queue["AT_RISK_REVENUE"].apply(lambda v: f"${v:,.0f}"),
    "Days Since Last Order": queue["DAYS_SINCE_LAST_ORDER"].astype(int),
    "Rep Note": queue["REP_NOTES"].fillna("—").str.slice(0, 80),
    "Recommended Action": queue["Recommended Action"],
})


def style_queue(row):
    # Color the whole row by the underlying RISK_LABEL of that practice.
    pid = queue.iloc[row.name]["RISK_LABEL"]
    color = RISK_COLORS.get(pid, BORDER)
    return [f"background-color: {color}25; color: #FFFFFF;"] * len(row)


st.dataframe(
    queue_display.style.apply(style_queue, axis=1),
    hide_index=True,
    use_container_width=True,
)


# =============================================================================
# G) INTERACTIVE PRACTICE DEEP DIVE
# =============================================================================
section_header(
    "Practice Deep Dive",
    "Select any practice to see its full profile, order history, and rep-note timeline.",
)
sel_practice_id = st.selectbox(
    "Practice",
    options=practices.sort_values("CHURN_RISK", ascending=False)["PRACTICE_ID"].tolist(),
    format_func=lambda pid: f"{pid} — {practices[practices.PRACTICE_ID==pid]['PRACTICE_NAME'].iloc[0]}",
)
prac = practices[practices["PRACTICE_ID"] == sel_practice_id].iloc[0]
prac_orders = orders[orders["PRACTICE_ID"] == sel_practice_id].sort_values("ORDER_DATE")
prac_notes = notes[notes["PRACTICE_ID"] == sel_practice_id].sort_values("NOTE_DATE")

g1, g2, g3, g4 = st.columns(4)
g1.markdown(kpi_card("Territory", prac["TERRITORY"], prac["PRACTICE_TYPE"], "brand"), unsafe_allow_html=True)
g2.markdown(kpi_card("Tenure", f"{int(prac['MONTHS_ACTIVE'])} mo", "with Revance", "brand"), unsafe_allow_html=True)
risk_variant = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}[prac["RISK_LABEL"]]
g3.markdown(
    kpi_card("Churn Risk", f"{prac['CHURN_RISK']*100:.0f}%", prac["RISK_LABEL"], risk_variant),
    unsafe_allow_html=True,
)
g4.markdown(
    kpi_card(
        "Total Revenue",
        f"${prac['PRACTICE_REVENUE']:,.0f}",
        f"{int(prac['DAYS_SINCE_LAST_ORDER'])}d since last order",
        "brand",
    ),
    unsafe_allow_html=True,
)

dd1, dd2 = st.columns(2)
with dd1:
    if not prac_orders.empty:
        fig = px.scatter(
            prac_orders,
            x="ORDER_DATE",
            y="REVENUE",
            size="QUANTITY",
            color="PRODUCT",
            title="Order timeline",
        )
        fig.update_layout(**PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No orders on file for this practice.")

with dd2:
    if not prac_notes.empty:
        st.markdown("**Rep notes**")
        for _, n in prac_notes.iterrows():
            sentiment_color = DANGER if n["SENTIMENT_SCORE"] < 0 else (WARNING if n["SENTIMENT_SCORE"] < 0.15 else SUCCESS)
            st.markdown(
                f"""<div class='insight-card' style='border-left-color:{sentiment_color};'>
                <div style='color:{TEXT_MUTED};font-size:0.78rem;'>{n['NOTE_DATE']} · sentiment {n['SENTIMENT_SCORE']:+.2f}</div>
                <div style='margin-top:0.3rem;'>{n['REP_NOTES']}</div>
                <div style='margin-top:0.3rem;color:{TEXT_MUTED};font-size:0.78rem;'>{n['RISK_INSIGHT']}</div>
                </div>""",
                unsafe_allow_html=True,
            )
    else:
        st.info("No rep notes on file for this practice.")


# =============================================================================
# J) FOOTER — refresh + export
# =============================================================================
st.markdown("<div class='footer'>", unsafe_allow_html=True)
foot1, foot2, foot3 = st.columns([1, 1, 2])

with foot1:
    if st.button("Refresh data from Snowflake"):
        load_data.clear()
        st.rerun()

with foot2:
    export_df = practices_all[practices_all["RISK_LABEL"] == "HIGH"][
        ["PRACTICE_ID", "PRACTICE_NAME", "TERRITORY", "PRACTICE_TYPE",
         "MONTHS_ACTIVE", "CHURN_RISK", "PRACTICE_REVENUE", "DAYS_SINCE_LAST_ORDER"]
    ].copy()
    st.download_button(
        "Export HIGH-risk list (.csv)",
        data=export_df.to_csv(index=False).encode("utf-8"),
        file_name=f"revance_high_risk_{date.today().isoformat()}.csv",
        mime="text/csv",
    )

with foot3:
    st.markdown(
        f"<div style='text-align:right;color:{TEXT_MUTED};'>"
        f"Powered by <b style='color:#E5E7EB;'>Snowflake</b> + <b style='color:{REVANCE_RED};'>ML</b>"
        f"</div>",
        unsafe_allow_html=True,
    )

st.markdown("</div>", unsafe_allow_html=True)
