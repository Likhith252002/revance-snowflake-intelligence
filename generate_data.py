import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

random.seed(42)
np.random.seed(42)

territories = ["Northeast", "Southeast", "Midwest", "West", "Southwest"]
products = ["DAXXIFY", "RHA2", "RHA3", "RHA4", "RHA_REDENSITY", "SKINPEN"]
practice_types = ["Dermatology", "Plastic Surgery", "Med Spa", "OB/GYN", "Primary Care"]

practices = []
for i in range(200):
    practice_id = f"P{str(i+1).zfill(4)}"
    territory = random.choice(territories)
    practice_type = random.choice(practice_types)
    months_active = random.randint(3, 36)
    churned = 1 if random.random() < 0.25 else 0
    practices.append({
        "PRACTICE_ID": practice_id,
        "PRACTICE_NAME": f"Practice_{i+1}",
        "TERRITORY": territory,
        "PRACTICE_TYPE": practice_type,
        "MONTHS_ACTIVE": months_active,
        "CHURNED": churned
    })

orders = []
for practice in practices:
    num_orders = random.randint(1, 20)
    for _ in range(num_orders):
        order_date = datetime.now() - timedelta(days=random.randint(1, 365))
        product = random.choice(products)
        quantity = random.randint(1, 10)
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
        "NOTE_DATE": (datetime.now() - timedelta(days=random.randint(1,30))).strftime("%Y-%m-%d"),
        "REP_NOTES": random.choice(note_templates)
    })

pd.DataFrame(practices).to_csv("practices.csv", index=False)
pd.DataFrame(orders).to_csv("orders.csv", index=False)
pd.DataFrame(rep_notes).to_csv("rep_notes.csv", index=False)
print("✅ Data generated: practices.csv, orders.csv, rep_notes.csv")