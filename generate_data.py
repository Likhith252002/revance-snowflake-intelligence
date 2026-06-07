"""
Synthetic dataset for the Revance Practice Intelligence Pipeline.

Generates three CSVs that mirror the shape of Revance's commercial data:
- practices.csv  : the customer master (dermatology, plastic surgery, med spa, ...)
- orders.csv     : product orders across DAXXIFY, the RHA filler family, and SkinPen
- rep_notes.csv  : free-text field notes captured by sales reps after a practice visit

Why this exists: the rest of the pipeline (Snowflake ingest, churn model, sentiment
analysis, dashboard) needs realistic data to run end-to-end without exposing any
actual CRM, order, or rep-CRM content. The distributions below (territory mix,
product split, ~25% historical churn rate, ~mix of positive/negative notes) are
tuned so the downstream model and dashboard show meaningful patterns.
"""

import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

random.seed(42)
np.random.seed(42)

# Revance ships nationally through a five-territory sales structure.
territories = ["Northeast", "Southeast", "Midwest", "West", "Southwest"]

# DAXXIFY is the headline neuromodulator; the RHA family covers dermal fillers
# (RHA2/3/4 for different injection depths, Redensity for fine lines); SkinPen
# is the microneedling device. Each practice typically anchors on 1–2 products.
products = ["DAXXIFY", "RHA2", "RHA3", "RHA4", "RHA_REDENSITY", "SKINPEN"]

# Who buys: aesthetic-focused specialties dominate, but OB/GYN and Primary Care
# show up in the long tail as med-spa-adjacent buyers.
practice_types = ["Dermatology", "Plastic Surgery", "Med Spa", "OB/GYN", "Primary Care"]

# --- Practices ---------------------------------------------------------------
# 200 practices is the scale of a regional book of business — large enough for
# the churn model to learn patterns, small enough to display in a single table.
practices = []
for i in range(200):
    practice_id = f"P{str(i+1).zfill(4)}"
    territory = random.choice(territories)
    practice_type = random.choice(practice_types)
    months_active = random.randint(3, 36)
    # ~25% historical churn rate matches what you'd expect in a mid-tier
    # aesthetics portfolio with active competitive pressure from Botox/Juvederm.
    churned = 1 if random.random() < 0.25 else 0
    practices.append({
        "PRACTICE_ID": practice_id,
        "PRACTICE_NAME": f"Practice_{i+1}",
        "TERRITORY": territory,
        "PRACTICE_TYPE": practice_type,
        "MONTHS_ACTIVE": months_active,
        "CHURNED": churned
    })

# --- Orders ------------------------------------------------------------------
# Order velocity is one of the strongest churn signals: a practice that used to
# order every month and now hasn't ordered in 90 days is on the edge.
orders = []
for practice in practices:
    num_orders = random.randint(1, 20)
    for _ in range(num_orders):
        order_date = datetime.now() - timedelta(days=random.randint(1, 365))
        product = random.choice(products)
        quantity = random.randint(1, 10)
        # Wide price band reflects the gap between SkinPen consumables and
        # high-end DAXXIFY/RHA4 orders.
        unit_price = random.uniform(200, 1500)
        orders.append({
            "ORDER_ID": f"ORD{len(orders)+1:05d}",
            "PRACTICE_ID": practice["PRACTICE_ID"],
            "PRODUCT": product,
            "ORDER_DATE": order_date.strftime("%Y-%m-%d"),
            "QUANTITY": quantity,
            "REVENUE": round(quantity * unit_price, 2),
            "TERRITORY": practice["TERRITORY"]
        })

# --- Rep notes ---------------------------------------------------------------
# Field notes are the qualitative signal — they catch competitor mentions and
# pricing pushback weeks before the order data shows a problem.
rep_notes = []
note_templates = [
    "Practice is ordering regularly, very satisfied with DAXXIFY results",
    "Concerned about pricing, may switch to competitor Botox",
    "Requested more training on injection techniques",
    "No orders in last 60 days, difficult to reach",
    "Expanding clinic, interested in bulk orders",
    "Complained about delivery delays last month",
    "Strong advocate, referring other practices to us",
    "Budget cuts mentioned, reducing orders significantly"
]
for practice in practices:
    rep_notes.append({
        "NOTE_ID": f"N{practice['PRACTICE_ID']}",
        "PRACTICE_ID": practice["PRACTICE_ID"],
        "TERRITORY": practice["TERRITORY"],
        "NOTE_DATE": (datetime.now() - timedelta(days=random.randint(1, 30))).strftime("%Y-%m-%d"),
        "REP_NOTES": random.choice(note_templates)
    })

pd.DataFrame(practices).to_csv("practices.csv", index=False)
pd.DataFrame(orders).to_csv("orders.csv", index=False)
pd.DataFrame(rep_notes).to_csv("rep_notes.csv", index=False)
print("Data generated: practices.csv, orders.csv, rep_notes.csv")
