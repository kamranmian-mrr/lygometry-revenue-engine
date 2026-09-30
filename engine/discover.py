import csv
import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "signals.csv"

FEEDS = {
    "Hacker News": "https://hnrss.org/newest",
    "Google News - AI": "https://news.google.com/rss/search?q=AI+problem+tool+workflow",
    "Google News - business": "https://news.google.com/rss/search?q=business+problem+software+workflow",
}

FIELDNAMES = [
    "signal_id",
    "source",
    "title",
    "link",
    "description",
    "published_at",
    "captured_at",
    "points",
    "comments",
]

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
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
        from email.utils import parsedate_to_datetime

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


def parse_feed(name, url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "LygometryRevenueEngine/0.2"},
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

    rows = list(merged.values())
    rows.sort(
        key=lambda row: row.get("published_at") or row.get("captured_at") or "",
        reverse=True,
    )
    return rows


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)

    new_rows = []
    for name, url in FEEDS.items():
        try:
            new_rows.extend(parse_feed(name, url))
        except Exception as error:
            print("WARN", name, error)

    existing_rows = load_existing_signals(OUT)

    if not new_rows:
        print("No new signals collected. Existing signals.csv was preserved.")
        return

    all_rows = merge_signals(existing_rows, new_rows)

    with OUT.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=FIELDNAMES,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"New signals collected: {len(new_rows)}")
    print(f"Existing signals loaded: {len(existing_rows)}")
    print(f"Unique signals stored: {len(all_rows)}")


if __name__ == "__main__":
    main()
