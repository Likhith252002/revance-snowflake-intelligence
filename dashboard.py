"""
Revance Practice Intelligence — enterprise dashboard.

This is the user-facing layer of the pipeline. Sales leadership lands here to
answer three questions, in order of urgency:

  1. Is the book healthy overall, and how much revenue is at risk right now?
  2. Which territories and products are dragging that number down?
  3. Which specific practices does a rep need to call this week, and why?

Everything below reads live from Snowflake and joins the model output
(CHURN_PREDICTIONS) with the qualitative signal (REP_NOTES_ANALYSIS) so each
row in the action queue carries both a probability and the rep's most recent
note. The UI is dark-themed in Revance brand colors with high-contrast HTML
cards so it can sit on a TV in the sales bullpen and still be readable from
across the room.
"""

from datetime import datetime, date
import re
import html as _html

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import snowflake.connector
import streamlit as st

from config import SNOWFLAKE_CONFIG

# --- Brand palette -----------------------------------------------------------
REVANCE_RED = "#C8102E"
REVANCE_RED_DEEP = "#8B0E20"
DARK_BG = "#0F0F0F"
PANEL_BG = "#1A1A1A"
CARD_BG = "#222222"
CARD_BG_RAISED = "#2B2B2B"
BORDER = "#333333"
TEXT_PRIMARY = "#F5F5F5"
TEXT_MUTED = "#9CA3AF"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#E5E7EB", family="Inter, -apple-system, sans-serif", size=13),
    margin=dict(l=10, r=10, t=50, b=10),
    title=dict(font=dict(size=15, color="#FFFFFF")),
)

RISK_COLORS = {"HIGH": DANGER, "MEDIUM": WARNING, "LOW": SUCCESS}
MODEL_ACCURACY = 0.72


st.set_page_config(
    page_title="Revance Practice Intelligence",
    page_icon="💉",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- CSS ---------------------------------------------------------------------
# High-contrast dark theme. Avoids translucent row tints (which wash out to
# near-white over a dark canvas) by using solid panel backgrounds + colored
# left borders and chips for status.
st.markdown(
    f"""
    <style>
        .stApp {{
            background-color: {DARK_BG};
            color: {TEXT_PRIMARY};
        }}
        section[data-testid="stSidebar"] {{
            background-color: #0B0B0B;
            border-right: 1px solid {BORDER};
        }}
        section[data-testid="stSidebar"] * {{ color: #E5E7EB !important; }}
        h1, h2, h3, h4 {{
            color: #FFFFFF !important;
            font-family: Inter, -apple-system, sans-serif;
            letter-spacing: -0.01em;
        }}
        .block-container {{ padding-top: 2rem !important; }}

        /* Section headers — bigger, clearer separation between sections */
        .section-header {{
            border-left: 4px solid {REVANCE_RED};
            padding: 0.1rem 0 0.1rem 1rem;
            margin: 2.5rem 0 1.25rem 0;
        }}
        .section-header h2 {{
            margin: 0; font-size: 1.5rem; font-weight: 700;
        }}
        .section-header p {{
            margin: 0.3rem 0 0 0; color: {TEXT_MUTED}; font-size: 0.92rem;
        }}

        /* KPI cards */
        .kpi {{
            background-color: {CARD_BG};
            border: 1px solid {BORDER};
            border-radius: 10px;
            padding: 1.1rem 1.25rem 1.15rem 1.25rem;
            min-height: 124px;
            position: relative;
        }}
        .kpi.high   {{ border-left: 4px solid {DANGER}; }}
        .kpi.medium {{ border-left: 4px solid {WARNING}; }}
        .kpi.low    {{ border-left: 4px solid {SUCCESS}; }}
        .kpi.brand  {{ border-left: 4px solid {REVANCE_RED}; }}
        .kpi-label {{
            color: {TEXT_MUTED}; font-size: 0.72rem;
            text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 0.5rem;
        }}
        .kpi-value {{
            color: #FFFFFF; font-size: 2rem; font-weight: 700; line-height: 1;
        }}
        .kpi-sub {{ color: {TEXT_MUTED}; font-size: 0.82rem; margin-top: 0.5rem; }}

        /* Executive banner */
        .exec-banner {{
            background: linear-gradient(135deg, #1F0A0E 0%, #3A0A14 55%, #5A1119 100%);
            border: 1px solid #4A1A22;
            border-radius: 16px;
            padding: 2rem 2.25rem;
            margin: 0.5rem 0 1.5rem 0;
            box-shadow: 0 6px 24px rgba(200, 16, 46, 0.15);
        }}
        .exec-label {{
            margin: 0; color: #E5C5C9; font-size: 0.78rem;
            letter-spacing: 0.18em; text-transform: uppercase; font-weight: 600;
        }}
        .exec-headline {{
            font-size: 3.5rem; font-weight: 800; color: #FFFFFF;
            line-height: 1; margin: 0.4rem 0 0 0; display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
        }}
        .exec-headline .of {{ color: rgba(255,255,255,0.55); font-size: 1.8rem; font-weight: 500; }}
        .pill {{
            display: inline-block; padding: 0.35rem 0.95rem; border-radius: 999px;
            font-weight: 700; font-size: 0.78rem; letter-spacing: 0.05em;
            text-transform: uppercase; vertical-align: middle;
        }}
        .exec-sub {{
            color: #F5F5F5; font-size: 1.1rem; margin: 0.9rem 0 0 0; line-height: 1.55;
        }}
        .exec-timestamp {{ color: rgba(255,255,255,0.5); font-size: 0.8rem; margin-top: 0.5rem; }}

        /* Takeaway strip — 3 callouts under the banner */
        .takeaway {{
            background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 10px;
            padding: 1rem 1.15rem; height: 100%;
        }}
        .takeaway-icon {{ font-size: 1.4rem; margin-bottom: 0.4rem; }}
        .takeaway-title {{
            color: {TEXT_MUTED}; font-size: 0.72rem;
            text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 0.4rem;
        }}
        .takeaway-body {{ color: #FFFFFF; font-size: 1rem; line-height: 1.4; }}
        .takeaway-body b {{ color: #FFFFFF; }}

        /* Territory leaderboard rows */
        .leader-row {{
            display: grid;
            grid-template-columns: 60px 1.5fr 1fr 1fr 1fr 1fr 1fr;
            gap: 1rem; align-items: center;
            background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px;
            padding: 1rem 1.25rem; margin-bottom: 0.6rem;
        }}
        .leader-row.crown {{
            background: linear-gradient(90deg, {CARD_BG_RAISED} 0%, {CARD_BG} 60%);
            border: 1px solid {REVANCE_RED};
        }}
        .leader-row.alarm {{ border-left: 4px solid {DANGER}; }}
        .leader-row.watch {{ border-left: 4px solid {WARNING}; }}
        .leader-row.good  {{ border-left: 4px solid {SUCCESS}; }}
        .leader-rank {{
            font-size: 1.6rem; font-weight: 800; color: {TEXT_MUTED}; text-align: center;
        }}
        .leader-row.crown .leader-rank {{ color: {REVANCE_RED}; }}
        .leader-name {{ font-size: 1.15rem; font-weight: 700; color: #FFFFFF; }}
        .leader-cell {{ color: #E5E7EB; font-size: 0.95rem; }}
        .leader-cell .lbl {{
            display: block; color: {TEXT_MUTED}; font-size: 0.7rem;
            text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.15rem;
        }}
        .leader-cell .val {{ font-weight: 700; }}

        /* Action queue rows */
        .action-row {{
            display: grid;
            grid-template-columns: 50px 2fr 1fr 1fr 1fr 2.5fr;
            gap: 1rem; align-items: center;
            background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px;
            padding: 1rem 1.25rem; margin-bottom: 0.6rem;
        }}
        .action-row.HIGH   {{ border-left: 4px solid {DANGER}; }}
        .action-row.MEDIUM {{ border-left: 4px solid {WARNING}; }}
        .action-row.LOW    {{ border-left: 4px solid {SUCCESS}; }}
        .action-rank {{
            font-size: 1.5rem; font-weight: 800; color: #FFFFFF; text-align: center;
        }}
        .action-practice {{ font-size: 1.05rem; font-weight: 700; color: #FFFFFF; }}
        .action-practice .terr {{
            display: block; color: {TEXT_MUTED}; font-size: 0.78rem; margin-top: 0.15rem; font-weight: 500;
        }}
        .action-num {{ color: #FFFFFF; font-weight: 700; font-size: 1.1rem; }}
        .action-num .lbl {{
            display: block; color: {TEXT_MUTED}; font-size: 0.68rem;
            text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.1rem; font-weight: 600;
        }}
        .action-note {{ color: #E5E7EB; font-size: 0.88rem; line-height: 1.4; }}
        .action-note .rec {{
            display: block; color: {REVANCE_RED}; font-weight: 700; margin-top: 0.3rem; font-size: 0.85rem;
        }}

        /* Insight callout */
        .insight-card {{
            background: {PANEL_BG}; border: 1px solid {BORDER};
            border-left: 3px solid {REVANCE_RED}; border-radius: 8px;
            padding: 1rem 1.2rem; color: #E5E7EB; margin: 0.75rem 0;
        }}

        /* Deep dive rep-note cards */
        .note-card {{
            background: {CARD_BG}; border: 1px solid {BORDER};
            border-left: 3px solid {REVANCE_RED}; border-radius: 8px;
            padding: 0.85rem 1rem; margin-bottom: 0.5rem;
        }}
        .note-meta {{ color: {TEXT_MUTED}; font-size: 0.78rem; }}
        .note-body {{ color: #FFFFFF; font-size: 0.95rem; margin-top: 0.3rem; }}
        .note-tag  {{ color: {TEXT_MUTED}; font-size: 0.78rem; margin-top: 0.3rem; }}

        .footer {{
            border-top: 1px solid {BORDER}; margin-top: 3rem; padding-top: 1.5rem;
            color: {TEXT_MUTED}; font-size: 0.85rem;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Data load ---------------------------------------------------------------
@st.cache_data(show_spinner="Loading from Snowflake...")
def load_data():
    conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
    cur = conn.cursor()
    cur.execute("USE WAREHOUSE COMPUTE_WH")

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

    cur.execute("SELECT ORDER_ID, PRACTICE_ID, PRODUCT, ORDER_DATE, QUANTITY, REVENUE, TERRITORY FROM ORDERS")
    orders = pd.DataFrame(
        cur.fetchall(),
        columns=["ORDER_ID", "PRACTICE_ID", "PRODUCT", "ORDER_DATE", "QUANTITY", "REVENUE", "TERRITORY"],
    )
    orders["ORDER_DATE"] = pd.to_datetime(orders["ORDER_DATE"])
    orders["REVENUE"] = orders["REVENUE"].astype(float)

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


# --- Per-practice revenue & recency (computed from full book) ---------------
practice_revenue = orders_all.groupby("PRACTICE_ID")["REVENUE"].sum().rename("PRACTICE_REVENUE")
practice_products = orders_all.groupby("PRACTICE_ID")["PRODUCT"].nunique().rename("PRODUCT_COUNT")
last_order = orders_all.groupby("PRACTICE_ID")["ORDER_DATE"].max().rename("LAST_ORDER")
today = pd.Timestamp(datetime.now().date())
days_since = (today - last_order).dt.days.rename("DAYS_SINCE_LAST_ORDER")

practices_all = (
    practices_all
    .merge(practice_revenue, on="PRACTICE_ID", how="left")
    .merge(practice_products, on="PRACTICE_ID", how="left")
    .merge(days_since, on="PRACTICE_ID", how="left")
    .fillna({"PRACTICE_REVENUE": 0, "PRODUCT_COUNT": 0, "DAYS_SINCE_LAST_ORDER": 365})
)


# --- Sidebar filters ---------------------------------------------------------
with st.sidebar:
    st.markdown(f"<h2 style='color:{REVANCE_RED};margin-top:0;'>Revance</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p style='color:#9CA3AF;margin-top:-12px;font-size:0.78rem;letter-spacing:0.1em;'>"
        "PRACTICE INTELLIGENCE</p>",
        unsafe_allow_html=True,
    )
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
        min_value=rev_min, max_value=rev_max, value=(rev_min, rev_max),
        step=max((rev_max - rev_min) // 50, 1),
    )

    st.markdown("---")
    st.markdown(
        f"<p style='color:{TEXT_MUTED};font-size:0.75rem;'>Filters apply to every chart on the page.</p>",
        unsafe_allow_html=True,
    )


mask = (
    practices_all["TERRITORY"].isin(sel_territory)
    & practices_all["RISK_LABEL"].isin(sel_risk)
    & practices_all["PRACTICE_TYPE"].isin(sel_type)
    & practices_all["PRACTICE_REVENUE"].between(sel_rev[0], sel_rev[1])
)
practices = practices_all[mask].copy()
orders = orders_all[orders_all["PRACTICE_ID"].isin(practices["PRACTICE_ID"])].copy()
notes = notes_all[notes_all["PRACTICE_ID"].isin(practices["PRACTICE_ID"])].copy()


if practices.empty:
    st.warning("No practices match the current filters. Loosen the sidebar selections to see data.")
    st.stop()


# --- Shared aggregates -------------------------------------------------------
high_risk = practices[practices["RISK_LABEL"] == "HIGH"]
med_risk = practices[practices["RISK_LABEL"] == "MEDIUM"]
low_risk = practices[practices["RISK_LABEL"] == "LOW"]

total_practices = len(practices)
high_pct = len(high_risk) / total_practices * 100 if total_practices else 0
med_pct = len(med_risk) / total_practices * 100 if total_practices else 0

# Portfolio Health Score — see ARCHITECTURE.md for the weighting rationale.
health_score = max(0, round(100 - high_pct * 1.0 - med_pct * 0.4))
health_color = SUCCESS if health_score >= 75 else (WARNING if health_score >= 60 else DANGER)
health_label = "Healthy" if health_score >= 75 else ("Watch" if health_score >= 60 else "At Risk")

at_risk_revenue = high_risk["PRACTICE_REVENUE"].sum()
total_revenue = practices["PRACTICE_REVENUE"].sum()
revenue_saved_potential = at_risk_revenue * 0.5


def section_header(title: str, subtitle: str = ""):
    sub_html = f"<p>{_html.escape(subtitle)}</p>" if subtitle else ""
    st.markdown(
        f"<div class='section-header'><h2>{_html.escape(title)}</h2>{sub_html}</div>",
        unsafe_allow_html=True,
    )


def kpi_card(label, value, sub="", variant="brand"):
    return f"""
    <div class='kpi {variant}'>
        <div class='kpi-label'>{_html.escape(label)}</div>
        <div class='kpi-value'>{value}</div>
        <div class='kpi-sub'>{_html.escape(sub)}</div>
    </div>
    """


# =============================================================================
# EXECUTIVE SUMMARY BANNER
# =============================================================================
st.markdown(
    f"""
    <div class='exec-banner'>
        <p class='exec-label'>Portfolio Health Score</p>
        <div class='exec-headline'>
            <span>{health_score}<span class='of'>/100</span></span>
            <span class='pill' style='background:{health_color};color:#0B0B0B;'>{health_label}</span>
        </div>
        <p class='exec-sub'>
            <b style='color:{DANGER};'>{len(high_risk)} high-risk practices</b> ·
            <b style='color:#FFFFFF;'>${at_risk_revenue/1_000_000:.1f}M at-risk revenue</b> ·
            <b style='color:#FFFFFF;'>${total_revenue/1_000_000:.1f}M total tracked</b>
        </p>
        <p class='exec-timestamp'>Last updated {datetime.now().strftime('%b %d, %Y · %I:%M %p')}</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# --- Takeaway strip — three at-a-glance callouts -----------------------------
worst_territory = (
    practices.groupby("TERRITORY")["RISK_LABEL"]
    .apply(lambda s: (s == "HIGH").sum())
    .idxmax()
)
worst_territory_count = (
    practices[practices["TERRITORY"] == worst_territory]["RISK_LABEL"].eq("HIGH").sum()
)
sent_by_terr_quick = notes.groupby("TERRITORY")["SENTIMENT_SCORE"].mean()
neg_terrs = sent_by_terr_quick[sent_by_terr_quick < 0.15].index.tolist()
neg_terr_text = ", ".join(neg_terrs) if neg_terrs else "None"

t1, t2, t3 = st.columns(3)
t1.markdown(
    f"""<div class='takeaway' style='border-left:3px solid {DANGER};'>
        <div class='takeaway-icon'>🚨</div>
        <div class='takeaway-title'>Most urgent territory</div>
        <div class='takeaway-body'><b>{worst_territory}</b> — {worst_territory_count} high-risk practices need calls this week.</div>
    </div>""",
    unsafe_allow_html=True,
)
t2.markdown(
    f"""<div class='takeaway' style='border-left:3px solid {WARNING};'>
        <div class='takeaway-icon'>💰</div>
        <div class='takeaway-title'>Save target this quarter</div>
        <div class='takeaway-body'>Cutting HIGH-risk churn in half would protect <b>${revenue_saved_potential/1_000_000:.2f}M</b> in revenue.</div>
    </div>""",
    unsafe_allow_html=True,
)
t3.markdown(
    f"""<div class='takeaway' style='border-left:3px solid {REVANCE_RED};'>
        <div class='takeaway-icon'>📉</div>
        <div class='takeaway-title'>Sentiment alarms</div>
        <div class='takeaway-body'><b>{neg_terr_text}</b> — rep-note sentiment is in the warning band.</div>
    </div>""",
    unsafe_allow_html=True,
)


# --- KPI strip --------------------------------------------------------------
section_header("Portfolio at a Glance", "Headline counts across the filtered book of business.")
c1, c2, c3, c4 = st.columns(4)
c1.markdown(kpi_card("Total Practices", f"{total_practices}", "across all territories", "brand"), unsafe_allow_html=True)
c2.markdown(kpi_card("High Risk", f"{len(high_risk)}", f"{high_pct:.0f}% of portfolio", "high"), unsafe_allow_html=True)
c3.markdown(kpi_card("Medium Risk", f"{len(med_risk)}", f"{med_pct:.0f}% of portfolio", "medium"), unsafe_allow_html=True)
c4.markdown(kpi_card("Total Revenue", f"${total_revenue/1_000_000:.1f}M", "trailing 12 months", "brand"), unsafe_allow_html=True)


# =============================================================================
# AT-RISK REVENUE
# =============================================================================
section_header(
    "At-Risk Revenue",
    "Dollar exposure from HIGH-risk practices and the upside if churn is halved.",
)
b1, b2, b3 = st.columns(3)
b1.markdown(
    kpi_card("Revenue at Risk", f"${at_risk_revenue/1_000_000:.2f}M",
             f"From {len(high_risk)} HIGH-risk practices", "high"),
    unsafe_allow_html=True,
)
b2.markdown(
    kpi_card("Potential Saved", f"${revenue_saved_potential/1_000_000:.2f}M",
             "If HIGH-risk churn drops 50%", "low"),
    unsafe_allow_html=True,
)
b3.markdown(
    kpi_card("Avg HIGH-risk practice", f"${(at_risk_revenue/max(len(high_risk),1))/1_000:.0f}K",
             "Average revenue per HIGH-risk account", "medium"),
    unsafe_allow_html=True,
)


# =============================================================================
# TERRITORY LEADERBOARD — rendered as solid HTML cards, not a tinted table
# =============================================================================
section_header(
    "Territory Leaderboard",
    "Ranked by health score so leadership knows where to deploy rep attention.",
)

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
terr["health_score"] = (100 - terr["high_risk_pct"] * 1.0).clip(lower=0).round(0).astype(int)
terr = terr.sort_values("health_score", ascending=False).reset_index(drop=True)


def health_class(score: int) -> str:
    if score >= 75:
        return "good"
    if score >= 60:
        return "watch"
    return "alarm"


def health_chip_color(score: int) -> str:
    return SUCCESS if score >= 75 else (WARNING if score >= 60 else DANGER)


leader_html = ""
for i, row in terr.iterrows():
    is_crown = i == 0
    cls = "leader-row " + health_class(row["health_score"])
    if is_crown:
        cls += " crown"
    rank_text = "👑" if is_crown else f"#{i+1}"
    chip = health_chip_color(row["health_score"])
    leader_html += f"""
    <div class='{cls}'>
        <div class='leader-rank'>{rank_text}</div>
        <div class='leader-name'>{_html.escape(row['TERRITORY'])}</div>
        <div class='leader-cell'>
            <span class='lbl'>Health</span>
            <span class='val' style='color:{chip};font-size:1.2rem;'>{row['health_score']}<span style='color:{TEXT_MUTED};font-size:0.85rem;'>/100</span></span>
        </div>
        <div class='leader-cell'>
            <span class='lbl'>Practices</span>
            <span class='val'>{int(row['practices'])}</span>
        </div>
        <div class='leader-cell'>
            <span class='lbl'>High Risk</span>
            <span class='val' style='color:{DANGER};'>{int(row['high_risk'])}</span>
        </div>
        <div class='leader-cell'>
            <span class='lbl'>Revenue</span>
            <span class='val'>${row['revenue']/1_000_000:.2f}M</span>
        </div>
        <div class='leader-cell'>
            <span class='lbl'>Avg Sentiment</span>
            <span class='val' style='color:{SUCCESS if row["avg_sentiment"] >= 0.15 else (WARNING if row["avg_sentiment"] >= 0 else DANGER)};'>
                {row['avg_sentiment']:+.2f}
            </span>
        </div>
    </div>
    """
st.markdown(leader_html, unsafe_allow_html=True)


# =============================================================================
# PRODUCT PENETRATION
# =============================================================================
section_header(
    "Product Penetration",
    "Practices buying across the line are stickier — single-product accounts churn first.",
)

single = practices[practices["PRODUCT_COUNT"] <= 1]
multi = practices[practices["PRODUCT_COUNT"] >= 3]
churn_single = single["CHURN_RISK"].mean() if len(single) else 0
churn_multi = multi["CHURN_RISK"].mean() if len(multi) else 0
delta_pct = ((churn_single - churn_multi) / churn_single * 100) if churn_single else 0

d1, d2 = st.columns([1, 1.5])
with d1:
    st.markdown(
        kpi_card("Single-Product Practices", f"{len(single)}",
                 f"avg churn risk {churn_single*100:.0f}%", "high"),
        unsafe_allow_html=True,
    )
    st.markdown(
        kpi_card("Multi-Product (3+) Practices", f"{len(multi)}",
                 f"avg churn risk {churn_multi*100:.0f}%", "low"),
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class='insight-card'>
        <b style='color:{REVANCE_RED};'>Insight:</b> Practices ordering 3+ products
        have <b style='color:#FFFFFF;'>{delta_pct:.0f}% lower</b> churn risk.
        The fastest path to portfolio stability is cross-sell from DAXXIFY anchors into RHA fillers.
        </div>""",
        unsafe_allow_html=True,
    )

with d2:
    prod_by_terr = (
        orders.groupby(["TERRITORY", "PRODUCT"])["PRACTICE_ID"]
        .nunique().reset_index(name="Practices")
    )
    fig = px.bar(
        prod_by_terr, x="TERRITORY", y="Practices", color="PRODUCT",
        title="Practices ordering each product, by territory",
        color_discrete_sequence=["#C8102E", "#E63946", "#F87171", "#FCA5A5", "#FECACA", "#FEE2E2"],
    )
    fig.update_layout(**PLOTLY_LAYOUT, legend=dict(bgcolor="rgba(0,0,0,0)", title=""))
    st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# REP PERFORMANCE
# =============================================================================
section_header(
    "Rep Performance",
    "Sentiment is a leading indicator — order data follows it by weeks.",
)
e1, e2 = st.columns([1.3, 1])

with e1:
    sent_avg = notes.groupby("TERRITORY")["SENTIMENT_SCORE"].mean().reset_index()
    sent_avg["color"] = sent_avg["SENTIMENT_SCORE"].apply(
        lambda v: DANGER if v < 0 else (WARNING if v < 0.15 else SUCCESS)
    )
    fig = px.bar(
        sent_avg, x="TERRITORY", y="SENTIMENT_SCORE",
        title="Avg rep-note sentiment by territory  (alarm < 0, watch < 0.15)",
    )
    fig.update_traces(marker_color=sent_avg["color"])
    fig.update_layout(**PLOTLY_LAYOUT, yaxis_title="Avg sentiment", xaxis_title="")
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
        top_keywords, x="Mentions", y="Keyword", orientation="h",
        title="Most common signals in rep notes",
        color="Mentions", color_continuous_scale=["#7A1D1D", REVANCE_RED, "#F87171"],
    )
    fig.update_layout(**PLOTLY_LAYOUT, yaxis=dict(categoryorder="total ascending", title=""), coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# MODEL CONFIDENCE
# =============================================================================
section_header(
    "Model Confidence",
    "Distribution of churn-risk scores plus the model's offline accuracy.",
)
f1, f2 = st.columns([1.4, 1])

with f1:
    fig = px.histogram(
        practices, x="CHURN_RISK", nbins=25, color="RISK_LABEL",
        color_discrete_map=RISK_COLORS,
        title="Churn-risk distribution across the portfolio",
    )
    fig.update_layout(**PLOTLY_LAYOUT, bargap=0.05, xaxis_title="Predicted churn probability",
                      legend=dict(bgcolor="rgba(0,0,0,0)", title=""))
    st.plotly_chart(fig, use_container_width=True)

with f2:
    gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=MODEL_ACCURACY * 100,
        number={"suffix": "%", "font": {"size": 48, "color": "#FFFFFF"}},
        title={"text": "Model Accuracy<br><span style='font-size:0.8em;color:#9CA3AF;'>held-out test set</span>",
               "font": {"color": "#FFFFFF", "size": 14}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "#9CA3AF"},
            "bar": {"color": REVANCE_RED, "thickness": 0.32},
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
# ACTION PRIORITY QUEUE — rendered as solid HTML cards
# =============================================================================
section_header(
    "Action Priority Queue",
    "The 10 practices the rep team should contact this week, ranked by churn risk × revenue.",
)

practices["AT_RISK_REVENUE"] = practices["CHURN_RISK"] * practices["PRACTICE_REVENUE"]
queue = practices.sort_values("AT_RISK_REVENUE", ascending=False).head(10).copy()

notes_sorted = notes.sort_values("NOTE_DATE", ascending=False).drop_duplicates("PRACTICE_ID")
queue = queue.merge(
    notes_sorted[["PRACTICE_ID", "REP_NOTES", "RISK_INSIGHT"]],
    on="PRACTICE_ID", how="left",
)


def recommended_action(row):
    insight = (row.get("RISK_INSIGHT") or "").upper()
    if "HIGH RISK" in insight:
        return "Schedule call this week — bring retention offer"
    if "MEDIUM RISK" in insight:
        return "Follow up on concerns within 14 days"
    if row["DAYS_SINCE_LAST_ORDER"] > 90:
        return "Re-engage — no orders in 90+ days"
    return "Quarterly check-in"


queue["Recommended Action"] = queue.apply(recommended_action, axis=1)

action_html = ""
for i, row in queue.iterrows():
    rank = list(queue.index).index(i) + 1
    risk_color = RISK_COLORS[row["RISK_LABEL"]]
    days = int(row["DAYS_SINCE_LAST_ORDER"])
    days_color = DANGER if days > 90 else (WARNING if days > 60 else TEXT_PRIMARY)
    note_text = (row["REP_NOTES"] or "—")
    note_text = (note_text[:90] + "…") if len(note_text) > 90 else note_text
    action_html += f"""
    <div class='action-row {row["RISK_LABEL"]}'>
        <div class='action-rank' style='color:{risk_color};'>{rank}</div>
        <div class='action-practice'>
            {_html.escape(row['PRACTICE_NAME'])}
            <span class='terr'>{_html.escape(row['TERRITORY'])} · {_html.escape(row['PRACTICE_TYPE'])}</span>
        </div>
        <div class='action-num'>
            <span class='lbl'>Churn Risk</span>
            <span style='color:{risk_color};'>{row['CHURN_RISK']*100:.0f}%</span>
        </div>
        <div class='action-num'>
            <span class='lbl'>At-Risk $</span>
            ${row['AT_RISK_REVENUE']:,.0f}
        </div>
        <div class='action-num'>
            <span class='lbl'>Days Quiet</span>
            <span style='color:{days_color};'>{days}d</span>
        </div>
        <div class='action-note'>
            "{_html.escape(note_text)}"
            <span class='rec'>→ {_html.escape(row['Recommended Action'])}</span>
        </div>
    </div>
    """
st.markdown(action_html, unsafe_allow_html=True)


# =============================================================================
# PRACTICE DEEP DIVE
# =============================================================================
section_header(
    "Practice Deep Dive",
    "Select any practice to see its full profile, order history, and rep notes.",
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
    kpi_card("Total Revenue", f"${prac['PRACTICE_REVENUE']:,.0f}",
             f"{int(prac['DAYS_SINCE_LAST_ORDER'])}d since last order", "brand"),
    unsafe_allow_html=True,
)

dd1, dd2 = st.columns(2)
with dd1:
    if not prac_orders.empty:
        fig = px.scatter(
            prac_orders, x="ORDER_DATE", y="REVENUE",
            size="QUANTITY", color="PRODUCT", title="Order timeline",
        )
        fig.update_layout(**PLOTLY_LAYOUT, legend=dict(bgcolor="rgba(0,0,0,0)", title=""))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No orders on file for this practice.")

with dd2:
    if not prac_notes.empty:
        st.markdown("**Rep notes**")
        for _, n in prac_notes.iterrows():
            sentiment_color = DANGER if n["SENTIMENT_SCORE"] < 0 else (WARNING if n["SENTIMENT_SCORE"] < 0.15 else SUCCESS)
            st.markdown(
                f"""<div class='note-card' style='border-left-color:{sentiment_color};'>
                <div class='note-meta'>{n['NOTE_DATE']} · sentiment {n['SENTIMENT_SCORE']:+.2f}</div>
                <div class='note-body'>{_html.escape(n['REP_NOTES'])}</div>
                <div class='note-tag'>{_html.escape(n['RISK_INSIGHT'])}</div>
                </div>""",
                unsafe_allow_html=True,
            )
    else:
        st.info("No rep notes on file for this practice.")


# =============================================================================
# FOOTER
# =============================================================================
st.markdown("<div class='footer'>", unsafe_allow_html=True)
foot1, foot2, foot3 = st.columns([1, 1, 2])

with foot1:
    if st.button("🔄 Refresh data from Snowflake"):
        load_data.clear()
        st.rerun()

with foot2:
    export_df = practices_all[practices_all["RISK_LABEL"] == "HIGH"][
        ["PRACTICE_ID", "PRACTICE_NAME", "TERRITORY", "PRACTICE_TYPE",
         "MONTHS_ACTIVE", "CHURN_RISK", "PRACTICE_REVENUE", "DAYS_SINCE_LAST_ORDER"]
    ].copy()
    st.download_button(
        "⬇ Export HIGH-risk list (.csv)",
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
