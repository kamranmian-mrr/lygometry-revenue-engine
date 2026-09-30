import csv
import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "signals.csv"


def google_news_url(query):
    return f"https://news.google.com/rss/search?q={quote_plus(query)}"


# Upgrade 2: broader, problem-led discovery themes. These extend the original
# feeds instead of replacing them.
FEEDS = {
    "Hacker News": {
        "url": "https://hnrss.org/newest",
        "theme": "open_technology_signals",
    },
    "Google News - AI": {
        "url": google_news_url("AI problem tool workflow"),
        "theme": "ai_workflow",
    },
    "Google News - business": {
        "url": google_news_url("business problem software workflow"),
        "theme": "business_workflow",
    },
    "Google News - unmet needs": {
        "url": google_news_url('"struggling to" business OR "cannot find" tool'),
        "theme": "unmet_needs",
    },
    "Google News - manual work": {
        "url": google_news_url('"manual process" problem OR "waste time" business'),
        "theme": "unmet_needs",
    },
    "Google News - comparison gaps": {
        "url": google_news_url('"hard to compare" software OR "best way to choose" technology'),
        "theme": "comparison_gaps",
    },
    "Google News - alternatives": {
        "url": google_news_url('"alternative to" expensive software'),
        "theme": "comparison_gaps",
    },
    "Google News - SMEs": {
        "url": google_news_url('"small business" workflow problem OR "SME" technology problem'),
        "theme": "underserved_users",
    },
    "Google News - government": {
        "url": google_news_url('"local government" process problem OR "government service" problem'),
        "theme": "underserved_users",
    },
    "Google News - Pakistan SMEs": {
        "url": google_news_url('"Pakistan SME" technology problem OR "Pakistan small business" digital'),
        "theme": "underserved_users",
    },
    "Google News - tool demand": {
        "url": google_news_url('"looking for a tool" business OR "need a solution" workflow'),
        "theme": "purchase_signals",
    },
    "Google News - willingness to pay": {
        "url": google_news_url('"would pay for" software OR "paying too much" software'),
        "theme": "purchase_signals",
    },
    "Google News - failed pilots": {
        "url": google_news_url('"pilot failed" technology OR "software does not work" business'),
        "theme": "failure_signals",
    },
    "Google News - spreadsheet problems": {
        "url": google_news_url('"spreadsheet problem" operations OR "spreadsheet chaos" business'),
        "theme": "failure_signals",
    },
}

FIELDNAMES = [
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
]

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}

PROBLEM_TERMS = {
    "problem",
    "problems",
    "struggle",
    "struggling",
    "difficult",
    "difficulty",
    "cannot",
    "can't",
    "fail",
    "failed",
    "failure",
    "manual",
    "waste",
    "inefficient",
    "bottleneck",
    "gap",
    "gaps",
    "risk",
    "cost",
    "expensive",
    "slow",
    "chaos",
    "broken",
    "missing",
    "shortage",
}

PURCHASE_TERMS = {
    "buy",
    "buyer",
    "buying",
    "pay",
    "paying",
    "paid",
    "price",
    "pricing",
    "budget",
    "subscription",
    "purchase",
    "procurement",
    "looking for",
    "need a solution",
    "alternative to",
}

TARGET_CUSTOMERS = {
    "small business": "Small business",
    "small businesses": "Small business",
    "sme": "SME",
    "smes": "SME",
    "startup": "Startup",
    "startups": "Startup",
    "enterprise": "Enterprise",
    "enterprises": "Enterprise",
    "government": "Government",
    "public sector": "Public sector",
    "local government": "Local government",
    "law firm": "Law firm",
    "legal department": "Legal department",
    "healthcare": "Healthcare",
    "hospital": "Healthcare",
    "accounting": "Accounting",
    "cfo": "Finance leadership",
    "cio": "Technology leadership",
    "developer": "Developer",
    "developers": "Developer",
    "farmer": "Agriculture",
    "farmers": "Agriculture",
    "university": "Higher education",
    "school": "Education",
}

THEME_WEIGHTS = {
    "purchase_signals": 3,
    "failure_signals": 2,
    "unmet_needs": 2,
    "underserved_users": 2,
    "comparison_gaps": 2,
    "ai_workflow": 1,
    "business_workflow": 1,
    "open_technology_signals": 0,
}


def clean_text(value):
    """Convert RSS HTML into readable single-line text."""
    if not value:
        return ""

    text = html.unescape(str(value))
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def clean_description(value, max_length=800):
    """Clean an RSS description and remove source-specific metadata noise."""
    text = clean_text(value)

    noise_patterns = [
        r"Article URL:\s*https?://\S+",
        r"Comments URL:\s*https?://\S+",
        r"Points:\s*\d+",
        r"#\s*Comments:\s*\d+",
    ]
    for pattern in noise_patterns:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text).strip(" -|;")
    if len(text) > max_length:
        shortened = text[:max_length].rsplit(" ", 1)[0].rstrip()
        text = shortened + "..."
    return text


def normalize_url(value):
    """Extract and normalize a usable HTTP(S) URL."""
    if not value:
        return ""

    text = html.unescape(str(value)).strip()
    match = re.search(r"https?://[^\s\"'<>]+", text, flags=re.IGNORECASE)
    if not match:
        return ""

    url = match.group(0).rstrip(".,;:!?)]}>")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return ""

        filtered_query = []
        for key, val in parse_qsl(parts.query, keep_blank_values=True):
            key_lower = key.lower()
            if key_lower.startswith("utm_") or key_lower in TRACKING_QUERY_KEYS:
                continue
            filtered_query.append((key, val))

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path or "",
                urlencode(filtered_query, doseq=True),
                "",
            )
        )
    except ValueError:
        return ""


def parse_published_date(item):
    """Return an ISO-8601 publication timestamp when the feed provides one."""
    raw_value = (
        item.findtext("pubDate", "")
        or item.findtext("{http://purl.org/dc/elements/1.1/}date", "")
        or item.findtext("{http://www.w3.org/2005/Atom}updated", "")
        or item.findtext("{http://www.w3.org/2005/Atom}published", "")
    ).strip()

    if not raw_value:
        return ""

    try:
        parsed = parsedate_to_datetime(raw_value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass

    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def create_signal_id(source, title, link):
    """Create a stable identifier for cross-run deduplication."""
    key = "|".join(
        [
            clean_text(source).casefold(),
            clean_text(title).casefold(),
            normalize_url(link).casefold(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def extract_integer(pattern, value):
    if not value:
        return ""
    decoded = html.unescape(str(value))
    match = re.search(pattern, decoded, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def extract_hacker_news_metadata(description):
    return {
        "points": extract_integer(r"Points:\s*(\d+)", description),
        "comments": extract_integer(r"#\s*Comments:\s*(\d+)", description),
    }


def text_from_first(item, tags):
    """Return the first available element text from a list of XML tag names."""
    for tag in tags:
        value = item.findtext(tag, "")
        if value and value.strip():
            return value.strip()
    return ""


def parse_feed(name, feed_config):
    req = urllib.request.Request(
        feed_config["url"],
        headers={"User-Agent": "LygometryRevenueEngine/0.3"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read()

    root = ET.fromstring(data)
    rows = []
    captured_at = datetime.now(timezone.utc).isoformat()

    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    for item in items[:30]:
        raw_title = text_from_first(
            item,
            ["title", "{http://www.w3.org/2005/Atom}title"],
        )
        raw_description = text_from_first(
            item,
            [
                "description",
                "{http://purl.org/rss/1.0/modules/content/}encoded",
                "{http://www.w3.org/2005/Atom}summary",
                "{http://www.w3.org/2005/Atom}content",
            ],
        )
        raw_link = text_from_first(item, ["link"])
        if not raw_link:
            atom_link = item.find("{http://www.w3.org/2005/Atom}link")
            if atom_link is not None:
                raw_link = atom_link.attrib.get("href", "")

        title = clean_text(raw_title)
        link = normalize_url(raw_link)
        if not title or not link:
            continue

        metadata = extract_hacker_news_metadata(raw_description)
        rows.append(
            {
                "signal_id": create_signal_id(name, title, link),
                "source": clean_text(name),
                "discovery_theme": feed_config["theme"],
                "title": title,
                "link": link,
                "description": clean_description(raw_description),
                "published_at": parse_published_date(item),
                "captured_at": captured_at,
                "points": metadata["points"] if name == "Hacker News" else "",
                "comments": metadata["comments"] if name == "Hacker News" else "",
            }
        )

    return rows


def load_existing_signals(output_file):
    """Load and normalize existing rows so old data is preserved."""
    if not output_file.exists():
        return []

    try:
        with output_file.open("r", newline="", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            existing = []

            for row in reader:
                source = clean_text(row.get("source", ""))
                title = clean_text(row.get("title", ""))
                link = normalize_url(row.get("link", ""))
                if not title or not link:
                    continue

                normalized = {field: row.get(field, "") for field in FIELDNAMES}
                normalized.update(
                    {
                        "signal_id": row.get("signal_id", "")
                        or create_signal_id(source, title, link),
                        "source": source,
                        "discovery_theme": row.get("discovery_theme", "")
                        or "legacy_unclassified",
                        "title": title,
                        "link": link,
                        "description": clean_description(row.get("description", "")),
                    }
                )
                existing.append(normalized)

            return existing
    except (OSError, csv.Error) as error:
        print(f"WARN existing signals could not be loaded: {error}")
        return []


def merge_signals(existing_signals, new_signals):
    """Merge data without duplicates, retaining useful values from either copy."""
    merged = {}

    for signal in existing_signals + new_signals:
        signal_id = signal.get("signal_id", "") or create_signal_id(
            signal.get("source", ""),
            signal.get("title", ""),
            signal.get("link", ""),
        )
        signal["signal_id"] = signal_id
        normalized = {field: signal.get(field, "") for field in FIELDNAMES}

        if signal_id not in merged:
            merged[signal_id] = normalized
            continue

        current = merged[signal_id]
        for field in FIELDNAMES:
            if normalized.get(field) and not current.get(field):
                current[field] = normalized[field]

    return list(merged.values())


def contains_term(text, terms):
    normalized = clean_text(text).casefold()
    return any(term.casefold() in normalized for term in terms)


def detect_target_customer(text):
    normalized = clean_text(text).casefold()
    detected = []
    for term, label in TARGET_CUSTOMERS.items():
        if term in normalized and label not in detected:
            detected.append(label)
    return "; ".join(detected[:4])


def calculate_recency_days(row, now):
    value = row.get("published_at") or row.get("captured_at")
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, (now - parsed.astimezone(timezone.utc)).days)
    except (ValueError, TypeError):
        return ""


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def add_deterministic_scoring(rows):
    """Upgrade 3: add transparent, factual prioritization fields.

    This is intentionally rules-based, not an AI judgment or revenue forecast.
    The score rewards explicit problem/purchase language, identifiable customers,
    recent publication, engagement, and repeated evidence within a theme.
    """
    now = datetime.now(timezone.utc)
    theme_sources = defaultdict(set)
    theme_counts = defaultdict(int)

    for row in rows:
        theme = row.get("discovery_theme") or "legacy_unclassified"
        theme_sources[theme].add(row.get("source", ""))
        theme_counts[theme] += 1

    for row in rows:
        combined_text = f"{row.get('title', '')} {row.get('description', '')}"
        problem_flag = contains_term(combined_text, PROBLEM_TERMS)
        purchase_flag = contains_term(combined_text, PURCHASE_TERMS)
        target_customer = detect_target_customer(combined_text)
        recency_days = calculate_recency_days(row, now)
        theme = row.get("discovery_theme") or "legacy_unclassified"
        source_count = len({source for source in theme_sources[theme] if source})
        theme_count = theme_counts[theme]
        points = safe_int(row.get("points"))
        comments = safe_int(row.get("comments"))

        score = THEME_WEIGHTS.get(theme, 0)
        score += 3 if problem_flag else 0
        score += 4 if purchase_flag else 0
        score += 2 if target_customer else 0

        if recency_days != "":
            if recency_days <= 2:
                score += 3
            elif recency_days <= 7:
                score += 2
            elif recency_days <= 30:
                score += 1

        if source_count >= 3:
            score += 2
        elif source_count >= 2:
            score += 1

        if theme_count >= 10:
            score += 2
        elif theme_count >= 3:
            score += 1

        if points >= 50 or comments >= 20:
            score += 3
        elif points >= 10 or comments >= 5:
            score += 2
        elif points > 0 or comments > 0:
            score += 1

        row["problem_language"] = "yes" if problem_flag else "no"
        row["purchase_language"] = "yes" if purchase_flag else "no"
        row["target_customer_detected"] = target_customer
        row["recency_days"] = recency_days
        row["source_count"] = source_count
        row["duplicate_theme_count"] = theme_count
        row["relevance_score"] = min(score, 20)

    rows.sort(
        key=lambda row: (
            safe_int(row.get("relevance_score")),
            row.get("published_at") or row.get("captured_at") or "",
        ),
        reverse=True,
    )
    return rows


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)

    new_rows = []
    successful_sources = 0
    for name, feed_config in FEEDS.items():
        try:
            feed_rows = parse_feed(name, feed_config)
            new_rows.extend(feed_rows)
            successful_sources += 1
            print(f"OK {name}: {len(feed_rows)} rows")
        except Exception as error:
            print("WARN", name, error)

    existing_rows = load_existing_signals(OUT)

    if not new_rows:
        print("No new signals collected. Existing signals.csv was preserved.")
        return

    all_rows = merge_signals(existing_rows, new_rows)
    all_rows = add_deterministic_scoring(all_rows)

    with OUT.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=FIELDNAMES,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Configured sources: {len(FEEDS)}")
    print(f"Successful sources: {successful_sources}")
    print(f"New signals collected: {len(new_rows)}")
    print(f"Existing signals loaded: {len(existing_rows)}")
    print(f"Unique signals stored: {len(all_rows)}")


if __name__ == "__main__":
    main()
