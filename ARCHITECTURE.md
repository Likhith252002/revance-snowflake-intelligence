# Architecture

This document describes how the Revance Practice Intelligence Pipeline moves data from raw synthetic source files through Snowflake, into two machine-learning workloads, and out to a live Streamlit dashboard for the commercial team.

---

## Data flow

```
        ┌───────────────────────┐
        │  generate_data.py     │   Synthesizes 200 practices,
        │  (faker-style seed)   │   ~2,000 orders, 200 rep notes
        └──────────┬────────────┘   modeled on Revance's footprint
                   │
                   ▼
     practices.csv · orders.csv · rep_notes.csv
                   │
                   ▼
        ┌───────────────────────┐
        │  setup_snowflake.py   │   CREATE OR REPLACE + write_pandas
        └──────────┬────────────┘
                   │
                   ▼
     ┌──────────────────────────────────────┐
     │            SNOWFLAKE                 │
     │  PRACTICES   ORDERS   REP_NOTES      │   (raw tables)
     └──────┬─────────────┬─────────────────┘
            │             │
            ▼             ▼
 ┌─────────────────┐  ┌─────────────────────┐
 │ churn_model.py  │  │ cortex_analysis.py  │
 │ Snowpark pull → │  │ Pull rep notes →    │
 │ sklearn RF →    │  │ TextBlob sentiment +│
 │ score every     │  │ rule-based risk tag │
 │ practice        │  │                     │
 └────────┬────────┘  └──────────┬──────────┘
          │                      │
          ▼                      ▼
 CHURN_PREDICTIONS       REP_NOTES_ANALYSIS
          │                      │
          └──────────┬───────────┘
                     │
                     ▼
        ┌────────────────────────┐
        │     dashboard.py       │   Streamlit + Plotly,
        │  (Snowflake-backed)    │   reads all 5 tables
        └────────────────────────┘
                     │
                     ▼
        Commercial leadership view:
        practice health, at-risk revenue,
        territory leaderboard, action queue
```

---

## Components

| Component | File | Purpose | Why it matters for Revance |
|---|---|---|---|
| Data generator | `generate_data.py` | Synthesizes practices, orders, and rep notes with realistic territory / product / sentiment patterns | Lets the pipeline run end-to-end without exposing real CRM or order data |
| Warehouse loader | `setup_snowflake.py` | Creates raw tables and bulk-loads the CSVs into Snowflake | Establishes Snowflake as the single source of truth — every downstream model and dashboard reads from here |
| Churn model | `churn_model.py` | Snowpark pulls features (tenure, order count, avg revenue, product variety), trains a Random Forest, writes per-practice probabilities back | Identifies the practices a rep should call **this week** before they switch to a competitor |
| Rep-note analysis | `cortex_analysis.py` | Scores free-text rep notes for sentiment and tags risk keywords (designed as a drop-in for `SNOWFLAKE.CORTEX.SENTIMENT`) | Surfaces qualitative signals that the model can't see — pricing pushback, delivery complaints, competitor mentions |
| Dashboard | `dashboard.py` | Streamlit UI reading all five tables; filters, leaderboards, action queue | Gives sales leadership a single place to triage the portfolio and assign follow-up |

---

## Snowflake schema

All tables live in `SNOWFLAKE_LEARNING_DB.PUBLIC`. Three are raw, two are derived.

### Raw tables

#### `PRACTICES`
The customer master. One row per medical-aesthetics practice.

| Column | Type | Description |
|---|---|---|
| `PRACTICE_ID` | VARCHAR | Primary key, e.g. `P0001` |
| `PRACTICE_NAME` | VARCHAR | Display name |
| `TERRITORY` | VARCHAR | Northeast / Southeast / Midwest / West / Southwest |
| `PRACTICE_TYPE` | VARCHAR | Dermatology, Plastic Surgery, Med Spa, OB/GYN, Primary Care |
| `MONTHS_ACTIVE` | INT | Tenure with Revance — primary churn signal |
| `CHURNED` | INT | Historical label (1 = churned). Training target. |

#### `ORDERS`
Transactional product orders.

| Column | Type | Description |
|---|---|---|
| `ORDER_ID` | VARCHAR | Primary key |
| `PRACTICE_ID` | VARCHAR | FK → `PRACTICES.PRACTICE_ID` |
| `PRODUCT` | VARCHAR | DAXXIFY, RHA2, RHA3, RHA4, RHA_REDENSITY, SKINPEN |
| `ORDER_DATE` | DATE | Used for recency / "days since last order" |
| `QUANTITY` | INT | Units ordered |
| `REVENUE` | FLOAT | Quantity × unit price in USD |
| `TERRITORY` | VARCHAR | Denormalized from `PRACTICES` for territory-level rollups |

#### `REP_NOTES`
Free-text field notes captured by sales reps during practice visits.

| Column | Type | Description |
|---|---|---|
| `NOTE_ID` | VARCHAR | Primary key |
| `PRACTICE_ID` | VARCHAR | FK → `PRACTICES.PRACTICE_ID` |
| `TERRITORY` | VARCHAR | Denormalized for territory-level sentiment |
| `NOTE_DATE` | DATE | When the rep logged the note |
| `REP_NOTES` | VARCHAR | The free-text body |

### Derived tables

#### `CHURN_PREDICTIONS`
Output of `churn_model.py`. Refreshed every model run.

| Column | Type | Description |
|---|---|---|
| `PRACTICE_ID` | VARCHAR | FK → `PRACTICES.PRACTICE_ID` |
| `CHURN_RISK` | FLOAT | Probability in `[0, 1]` from the Random Forest |
| `RISK_LABEL` | VARCHAR | `HIGH` (>0.6), `MEDIUM` (0.35–0.6), `LOW` (<0.35) — the buckets sales leadership uses to assign follow-up |

#### `REP_NOTES_ANALYSIS`
Output of `cortex_analysis.py`. One row per rep note.

| Column | Type | Description |
|---|---|---|
| `NOTE_ID` | VARCHAR | FK → `REP_NOTES.NOTE_ID` |
| `PRACTICE_ID` | VARCHAR | FK → `PRACTICES.PRACTICE_ID` |
| `TERRITORY` | VARCHAR | For territory-level sentiment averages |
| `REP_NOTES` | VARCHAR | The original note (denormalized for convenience) |
| `SENTIMENT_SCORE` | FLOAT | TextBlob polarity in `[-1, 1]` |
| `RISK_INSIGHT` | VARCHAR | Rule-based tag: HIGH / MEDIUM / LOW risk explanation |

---

## Refresh cadence

The pipeline is currently single-shot — re-run the scripts in order whenever the source data changes:

1. `generate_data.py` (only if regenerating the synthetic dataset)
2. `setup_snowflake.py`
3. `churn_model.py`
4. `cortex_analysis.py`
5. `dashboard.py` reads live from Snowflake on each page load (cached for the session)

In a production deployment, steps 2–4 would be scheduled as Snowflake Tasks or an external orchestrator (Airflow, Dagster, etc.) so predictions stay fresh as new orders and notes land.
