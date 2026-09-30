import csv
import hashlib
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_FILE = ROOT / "data" / "signals.csv"
OPPORTUNITIES_FILE = ROOT / "data" / "opportunities.csv"
MAP_FILE = ROOT / "data" / "opportunity_signal_map.csv"

REQUIRED_SIGNAL_COLUMNS = {
    "signal_id",
    "source",
    "discovery_theme",
    "title",
    "link",
    "description",
    "published_at",
    "captured_at",
    "points",
    "comments",
    "problem_language",
    "purchase_language",
    "target_customer_detected",
    "recency_days",
    "source_count",
    "duplicate_theme_count",
    "relevance_score",
}

OPPORTUNITY_FIELDS = [
    "opportunity_id",
    "opportunity_title",
    "discovery_theme",
    "problem_statement",
    "target_customer",
    "topic_key",
    "signal_count",
    "independent_source_count",
    "latest_published_at",
    "median_recency_days",
    "problem_evidence_count",
    "purchase_evidence_count",
    "customer_evidence_count",
    "highest_signal_score",
    "average_signal_score",
    "evidence_links",
    "representative_signal_ids",
    "opportunity_score",
    "confidence_level",
    "status",
    "created_at",
    "updated_at",
]

MAP_FIELDS = [
    "opportunity_id",
    "signal_id",
    "match_method",
    "match_score",
    "added_at",
]

MIN_RELEVANCE_SCORE = 6
MAX_EVIDENCE_LINKS = 5
MAX_SIGNAL_IDS = 10
MIN_CLUSTER_SIZE = 2

ALLOWED_EXISTING_STATUSES = {
    "discovered",
    "monitor",
    "ready_for_review",
    "experiment_candidate",
    "rejected",
}

CUSTOMER_NORMALIZATION = {
    "small business": "SME",
    "small businesses": "SME",
    "smb": "SME",
    "smbs": "SME",
    "sme": "SME",
    "smes": "SME",
    "msme": "SME",
    "msmes": "SME",
    "startup": "Startup",
    "startups": "Startup",
    "government": "Government",
    "local government": "Government",
    "public sector": "Government",
    "government department": "Government",
    "government departments": "Government",
    "law firm": "Legal",
    "law firms": "Legal",
    "legal department": "Legal",
    "legal departments": "Legal",
    "higher education": "Education",
    "education": "Education",
    "university": "Education",
    "universities": "Education",
    "healthcare": "Healthcare",
    "developer": "Developer",
    "developers": "Developer",
    "enterprise": "Enterprise",
    "technology leadership": "Technology leadership",
    "finance leadership": "Finance leadership",
    "agriculture": "Agriculture",
}

THEME_TITLES = {
    "underserved_users": "Underserved Customer Need",
    "comparison_gaps": "Product Comparison and Alternative Gap",
    "unmet_needs": "Unmet Operational Need",
    "purchase_signals": "Purchase and Tool Demand",
    "failure_signals": "Failed Process or Pilot",
    "open_technology_signals": "Open Technology Opportunity",
    "business_workflow": "Business Workflow Improvement",
    "ai_workflow": "AI Workflow Improvement",
    "legacy_unclassified": "Emerging Opportunity Signal",
}

THEME_PROBLEM_PHRASES = {
    "underserved_users": "an underserved need affecting",
    "comparison_gaps": "difficulty comparing, replacing, or selecting solutions for",
    "unmet_needs": "a recurring unmet operational need affecting",
    "purchase_signals": "purchase or tool-selection demand among",
    "failure_signals": "a recurring process, spreadsheet, or pilot failure affecting",
    "open_technology_signals": "an emerging open-technology need affecting",
    "business_workflow": "a business-workflow improvement need affecting",
    "ai_workflow": "an AI-workflow implementation or governance need affecting",
    "legacy_unclassified": "an emerging issue affecting",
}

STOPWORDS = {
    "a", "about", "after", "all", "an", "and", "are", "as", "at", "be",
    "before", "best", "beyond", "but", "by", "can", "do", "does", "for",
    "from", "get", "gets", "getting", "guide", "has", "have", "help", "how",
    "i", "in", "into", "is", "it", "its", "more", "most", "new", "not",
    "of", "on", "or", "our", "out", "over", "review", "should", "software",
    "some", "than", "that", "the", "their", "these", "this", "to", "tool",
    "tools", "top", "use", "using", "vs", "what", "when", "which", "who",
    "why", "with", "without", "you", "your", "2024", "2025", "2026",
}

TOPIC_RULES = [
    ("ai_adoption", {"ai", "artificial", "intelligence", "agent", "agents", "copilot", "llm", "genai"}),
    ("workflow_automation", {"workflow", "workflows", "automation", "automate", "process", "processes", "manual"}),
    ("data_governance", {"data", "governance", "compliance", "security", "risk", "shadow", "privacy"}),
    ("spreadsheets", {"spreadsheet", "spreadsheets", "excel"}),
    ("roi_cost", {"roi", "cost", "costs", "pricing", "price", "expensive", "budget", "spend", "fee", "fees", "pay"}),
    ("alternatives", {"alternative", "alternatives", "replacement", "replace", "open-source", "opensource", "free"}),
    ("sales_growth", {"sales", "growth", "marketing", "lead", "leads", "revenue", "buyers"}),
    ("government_services", {"government", "public", "city", "cities", "county", "policy", "governance"}),
    ("education_skills", {"education", "university", "universities", "student", "students", "training", "skills", "learning"}),
    ("healthcare", {"healthcare", "health", "clinical", "hospital", "medical", "patient", "patients"}),
    ("finance_operations", {"finance", "financial", "accounting", "tax", "payroll", "payment", "payments", "budgeting"}),
    ("productivity", {"productivity", "efficiency", "operations", "operational", "delivery"}),
]

QUERY_COLLISION_PATTERNS = {
    "failure_signals": [
        re.compile(r"\bpilot\b.*\b(drug|alcohol|airline|aircraft|flight|crash|landing|fog|aviation)\b", re.I),
        re.compile(r"\b(airline|aircraft|flight|crash|aviation)\b.*\bpilot\b", re.I),
    ]
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    return "" if value is None else str(value).strip()


def to_int(value, default=0):
    try:
        return max(0, int(float(clean(value))))
    except (TypeError, ValueError):
        return default


def to_float(value, default=0.0):
    try:
        return float(clean(value))
    except (TypeError, ValueError):
        return default


def parse_timestamp(value):
    value = clean(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def is_valid_url(value):
    try:
        parsed = urlsplit(clean(value))
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def normalize_customer(value):
    parts = [part.strip() for part in re.split(r"[;,|/]", clean(value)) if part.strip()]
    if not parts:
        return "General business"
    normalized = []
    for part in parts:
        key = re.sub(r"\s+", " ", part.casefold())
        customer = CUSTOMER_NORMALIZATION.get(key, part.strip().title())
        if customer not in normalized:
            normalized.append(customer)
    return "; ".join(normalized[:3])


def tokenize(text):
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", clean(text).casefold())
    return [word for word in words if len(word) > 2 and word not in STOPWORDS]


def topic_key_for(signal):
    text = " ".join(
        [clean(signal.get("title")), clean(signal.get("description")), clean(signal.get("target_customer_detected"))]
    )
    tokens = tokenize(text)
    token_set = set(tokens)
    scored = []
    for key, vocabulary in TOPIC_RULES:
        score = len(token_set & vocabulary)
        if score:
            scored.append((score, key))
    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[0][1]
    return "general"


def is_query_collision(signal):
    theme = clean(signal.get("discovery_theme"))
    text = f"{clean(signal.get('title'))} {clean(signal.get('description'))}"
    return any(pattern.search(text) for pattern in QUERY_COLLISION_PATTERNS.get(theme, []))


def eligible(signal):
    score = to_int(signal.get("relevance_score"))
    if score < MIN_RELEVANCE_SCORE:
        return False
    if not clean(signal.get("signal_id")) or not clean(signal.get("title")):
        return False
    if is_query_collision(signal):
        return False
    theme = clean(signal.get("discovery_theme"))
    has_evidence = (
        clean(signal.get("problem_language")).casefold() == "yes"
        or clean(signal.get("purchase_language")).casefold() == "yes"
        or bool(clean(signal.get("target_customer_detected")))
        or theme != "legacy_unclassified"
    )
    return has_evidence


def stable_id(*parts):
    key = "|".join(clean(part).casefold() for part in parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def load_signals():
    if not SIGNALS_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {SIGNALS_FILE}")
    with SIGNALS_FILE.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_SIGNAL_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError("Missing required signal columns: " + ", ".join(sorted(missing)))
        rows = []
        malformed = 0
        for row in reader:
            if row.get(None):
                malformed += 1
                continue
            rows.append(row)
    return rows, malformed


def load_existing_opportunities():
    if not OPPORTUNITIES_FILE.exists():
        return {}
    with OPPORTUNITIES_FILE.open("r", newline="", encoding="utf-8-sig") as handle:
        return {
            clean(row.get("opportunity_id")): row
            for row in csv.DictReader(handle)
            if clean(row.get("opportunity_id"))
        }


def independent_source(signal):
    source = clean(signal.get("source"))
    title = clean(signal.get("title"))
    if title and " - " in title:
        publisher = title.rsplit(" - ", 1)[-1].strip()
        if publisher:
            return publisher.casefold()
    return source.casefold()


def score_cluster(signals, customer, theme):
    count = len(signals)
    independent_count = len({independent_source(signal) for signal in signals})
    problem_count = sum(clean(s.get("problem_language")).casefold() == "yes" for s in signals)
    purchase_count = sum(clean(s.get("purchase_language")).casefold() == "yes" for s in signals)
    scored = [to_int(s.get("relevance_score")) for s in signals]
    recencies = [to_int(s.get("recency_days")) for s in signals if clean(s.get("recency_days"))]

    if count >= 13:
        breadth = 5
    elif count >= 8:
        breadth = 4
    elif count >= 5:
        breadth = 3
    elif count >= 3:
        breadth = 2
    elif count == 2:
        breadth = 1
    else:
        breadth = 0

    problem_ratio = problem_count / count if count else 0
    purchase_ratio = purchase_count / count if count else 0
    problem_strength = 3 if problem_ratio >= 0.6 else 2 if problem_ratio >= 0.35 else 1 if problem_count else 0
    purchase_strength = 3 if purchase_ratio >= 0.5 else 2 if purchase_ratio >= 0.25 else 1 if purchase_count else 0

    median_recency = int(statistics.median(recencies)) if recencies else 9999
    recency_score = 3 if median_recency <= 30 else 2 if median_recency <= 90 else 1 if median_recency <= 365 else 0
    customer_score = 0 if customer == "General business" else 2 if ";" not in customer else 1
    theme_score = 2 if theme != "legacy_unclassified" else 0
    quality_ratio = sum(bool(clean(s.get("description"))) and bool(parse_timestamp(s.get("published_at"))) for s in signals) / count
    quality_score = 2 if quality_ratio >= 0.7 else 1 if quality_ratio >= 0.35 else 0

    total = breadth + problem_strength + purchase_strength + recency_score + customer_score + theme_score + quality_score
    return min(20, total), median_recency, independent_count, problem_count, purchase_count, scored


def confidence_for(signal_count, independent_count, problem_count, customer):
    if signal_count >= 5 and independent_count >= 3 and problem_count >= 1 and customer != "General business":
        return "supported"
    if signal_count >= 3 and independent_count >= 2:
        return "emerging"
    return "low"


def status_for(score, confidence, existing_status=""):
    if existing_status in {"experiment_candidate", "rejected"}:
        return existing_status
    if score >= 13 and confidence in {"emerging", "supported"}:
        return "ready_for_review"
    if score >= 8:
        return "monitor"
    return "discovered"


def pretty_topic(topic):
    labels = {
        "ai_adoption": "AI Adoption",
        "workflow_automation": "Workflow Automation",
        "data_governance": "Data Governance",
        "spreadsheets": "Spreadsheet Risk",
        "roi_cost": "ROI and Cost Control",
        "alternatives": "Affordable Alternatives",
        "sales_growth": "Sales and Growth",
        "government_services": "Government Services",
        "education_skills": "Education and Skills",
        "healthcare": "Healthcare",
        "finance_operations": "Finance Operations",
        "productivity": "Operational Productivity",
        "general": "General Need",
    }
    return labels.get(topic, topic.replace("_", " ").title())


def build_title(theme, customer, topic):
    topic_label = pretty_topic(topic)
    if customer != "General business":
        return f"{customer}: {topic_label}"
    return f"{THEME_TITLES.get(theme, 'Opportunity')}: {topic_label}"


def build_problem_statement(theme, customer, topic, signal_count, independent_count):
    phrase = THEME_PROBLEM_PHRASES.get(theme, "a recurring issue affecting")
    customer_phrase = customer if customer != "General business" else "business users"
    return (
        f"{signal_count} eligible signals from {independent_count} independent source(s) "
        f"indicate {phrase} {customer_phrase}, focused on {pretty_topic(topic).lower()}. "
        "This is an evidence candidate, not validated customer demand."
    )


def build_outputs(signals, existing):
    clusters = defaultdict(list)
    mappings = []
    generated_at = now_iso()

    for signal in signals:
        if not eligible(signal):
            continue
        theme = clean(signal.get("discovery_theme")) or "legacy_unclassified"
        customer = normalize_customer(signal.get("target_customer_detected"))
        topic = topic_key_for(signal)
        clusters[(theme, customer, topic)].append(signal)

    opportunities = []
    for (theme, customer, topic), cluster_signals in sorted(clusters.items()):
        if len(cluster_signals) < MIN_CLUSTER_SIZE:
            continue

        # Exact headline repeats within a cluster count only once.
        unique = {}
        for signal in sorted(cluster_signals, key=lambda s: to_int(s.get("relevance_score")), reverse=True):
            headline_key = re.sub(r"\W+", " ", clean(signal.get("title")).casefold()).strip()
            unique.setdefault(headline_key, signal)
        cluster_signals = list(unique.values())
        if len(cluster_signals) < MIN_CLUSTER_SIZE:
            continue

        opportunity_id = stable_id(theme, customer, topic)
        previous = existing.get(opportunity_id, {})
        score, median_recency, independent_count, problem_count, purchase_count, signal_scores = score_cluster(
            cluster_signals, customer, theme
        )
        confidence = confidence_for(len(cluster_signals), independent_count, problem_count, customer)
        status = status_for(score, confidence, clean(previous.get("status")))

        dated = [parse_timestamp(s.get("published_at")) for s in cluster_signals]
        dated = [d for d in dated if d]
        latest_published = max(dated).isoformat() if dated else ""
        customer_count = sum(bool(clean(s.get("target_customer_detected"))) for s in cluster_signals)

        ranked = sorted(
            cluster_signals,
            key=lambda s: (
                to_int(s.get("relevance_score")),
                -(to_int(s.get("recency_days"), 9999)),
            ),
            reverse=True,
        )
        links = []
        for signal in ranked:
            link = clean(signal.get("link"))
            if is_valid_url(link) and link not in links:
                links.append(link)
            if len(links) >= MAX_EVIDENCE_LINKS:
                break
        representative_ids = [clean(s.get("signal_id")) for s in ranked[:MAX_SIGNAL_IDS]]

        created_at = clean(previous.get("created_at")) or generated_at
        opportunities.append(
            {
                "opportunity_id": opportunity_id,
                "opportunity_title": build_title(theme, customer, topic),
                "discovery_theme": theme,
                "problem_statement": build_problem_statement(
                    theme, customer, topic, len(cluster_signals), independent_count
                ),
                "target_customer": customer,
                "topic_key": topic,
                "signal_count": len(cluster_signals),
                "independent_source_count": independent_count,
                "latest_published_at": latest_published,
                "median_recency_days": median_recency,
                "problem_evidence_count": problem_count,
                "purchase_evidence_count": purchase_count,
                "customer_evidence_count": customer_count,
                "highest_signal_score": max(signal_scores) if signal_scores else 0,
                "average_signal_score": round(statistics.mean(signal_scores), 2) if signal_scores else 0,
                "evidence_links": " | ".join(links),
                "representative_signal_ids": " | ".join(representative_ids),
                "opportunity_score": score,
                "confidence_level": confidence,
                "status": status,
                "created_at": created_at,
                "updated_at": generated_at,
            }
        )

        for signal in cluster_signals:
            signal_tokens = set(tokenize(f"{clean(signal.get('title'))} {clean(signal.get('description'))}"))
            vocabulary = next((v for key, v in TOPIC_RULES if key == topic), set())
            overlap = len(signal_tokens & vocabulary)
            match_score = min(100, 55 + overlap * 10 + (10 if normalize_customer(signal.get("target_customer_detected")) == customer else 0))
            mappings.append(
                {
                    "opportunity_id": opportunity_id,
                    "signal_id": clean(signal.get("signal_id")),
                    "match_method": "theme+customer+keyword",
                    "match_score": match_score,
                    "added_at": generated_at,
                }
            )

    opportunities.sort(
        key=lambda row: (
            to_int(row["opportunity_score"]),
            to_int(row["signal_count"]),
            row["opportunity_title"],
        ),
        reverse=True,
    )
    mappings.sort(key=lambda row: (row["opportunity_id"], row["signal_id"]))
    return opportunities, mappings


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    try:
        signals, malformed_count = load_signals()
        existing = load_existing_opportunities()
        opportunities, mappings = build_outputs(signals, existing)
        write_csv(OPPORTUNITIES_FILE, OPPORTUNITY_FIELDS, opportunities)
        write_csv(MAP_FILE, MAP_FIELDS, mappings)
    except (OSError, ValueError, FileNotFoundError) as error:
        print(f"ERROR: {error}")
        return 1

    print("=== Opportunity Candidate Engine ===")
    print(f"Signals read: {len(signals)}")
    print(f"Malformed signal rows skipped: {malformed_count}")
    print(f"Opportunity candidates written: {len(opportunities)}")
    print(f"Opportunity-signal mappings written: {len(mappings)}")
    print(f"Output: {OPPORTUNITIES_FILE}")
    print(f"Output: {MAP_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
