from dataclasses import dataclass
from typing import Dict

WEIGHTS = {
    "demand": 25,
    "pain": 20,
    "monetization": 15,
    "testability": 15,
    "automation": 15,
    "gap": 10,
}

@dataclass
class Opportunity:
    name: str
    demand: float
    pain: float
    monetization: float
    testability: float
    automation: float
    gap: float
    risk_penalty: float = 0.0

    def score(self) -> float:
        raw = sum(getattr(self, k) * w / 10 for k, w in WEIGHTS.items())
        return round(max(0, min(100, raw - self.risk_penalty)), 2)

def score_row(row: Dict[str, str]) -> float:
    o = Opportunity(
        name=row.get("name", ""),
        demand=float(row.get("demand", 0)),
        pain=float(row.get("pain", 0)),
        monetization=float(row.get("monetization", 0)),
        testability=float(row.get("testability", 0)),
        automation=float(row.get("automation", 0)),
        gap=float(row.get("gap", 0)),
        risk_penalty=float(row.get("risk_penalty", 0)),
    )
    return o.score()

if __name__ == "__main__":
    demo = Opportunity(
        "Example information gap",
        demand=8, pain=8, monetization=6,
        testability=10, automation=8, gap=7
    )
    print({"example_score": demo.score()})
