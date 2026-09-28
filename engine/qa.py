import csv
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
required=["name","hypothesis","source","evidence","demand","pain","monetization",
          "testability","automation","gap","risk_penalty","status"]

p=ROOT/"data"/"opportunities.csv"
with p.open(encoding="utf-8") as f:
    rows=list(csv.DictReader(f))

errors=[]
for i,r in enumerate(rows,2):
    for c in required:
        if c not in r: errors.append(f"row {i}: missing {c}")
    for c in ["demand","pain","monetization","testability","automation","gap","risk_penalty"]:
        try: float(r[c])
        except: errors.append(f"row {i}: {c} is not numeric")

print("QA PASS" if not errors else "QA FAIL")
for e in errors: print(e)
raise SystemExit(1 if errors else 0)
