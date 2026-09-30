import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_FILE = ROOT / "data" / "signals.csv"

REQUIRED_COLUMNS = [
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

ALLOWED_DISCOVERY_THEMES = {
    "legacy_unclassified",
    "underserved_users",
    "comparison_gaps",
    "unmet_needs",
    "purchase_signals",
    "failure_signals",
    "open_technology_signals",
    "business_workflow",
    "ai_workflow",
}

SIGNAL_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
HTML_PATTERN = re.compile(
    r"<[^>]+>|&lt;/?[a-zA-Z][^&]*&gt;",
    flags=re.IGNORECASE,
)
BOOLEAN_VALUES = {"yes", "no", ""}

# Transitional policy: these are reported but do not fail the run yet.
# Set STRICT_URLS = True after discover.py is confirmed to preserve http:// or https://.
STRICT_URLS = False
# Set STRICT_ENRICHMENT = True after all migrated rows contain valid enrichment values.
STRICT_ENRICHMENT = False


def safe_value(row, column):
    value = row.get(column, "")
    return "" if value is None else str(value).strip()


def is_valid_timestamp(value, allow_blank=False):
    value = (value or "").strip()
    if not value:
        return allow_blank
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def is_valid_http_url(value):
    value = (value or "").strip()
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.netloc)
        )
    except ValueError:
        return False


def is_nonnegative_integer(value, allow_blank=False):
    value = (value or "").strip()
    if not value:
        return allow_blank
    return value.isdigit()


def print_examples(title, examples, limit=5):
    if not examples:
        return
    print(f"\n{title}:")
    for example in examples[:limit]:
        print(f"  - {example}")
    remaining = len(examples) - limit
    if remaining > 0:
        print(f"  ... and {remaining} more")


def main():
    print("=== Lygometry Signals QA ===")
    print(f"File: {SIGNALS_FILE}")
    print(
        "Policy: "
        f"STRICT_URLS={STRICT_URLS}, "
        f"STRICT_ENRICHMENT={STRICT_ENRICHMENT}"
    )

    if not SIGNALS_FILE.exists():
        print("::error::data/signals.csv does not exist.")
        return 1

    try:
        with SIGNALS_FILE.open(
            "r", newline="", encoding="utf-8-sig"
        ) as csvfile:
            reader = csv.DictReader(csvfile)
            actual_columns = reader.fieldnames or []
            rows = list(reader)
    except (OSError, csv.Error) as error:
        print(f"::error::Could not read signals.csv: {error}")
        return 1

    missing_columns = [
        column for column in REQUIRED_COLUMNS
        if column not in actual_columns
    ]
    unexpected_columns = [
        column for column in actual_columns
        if column not in REQUIRED_COLUMNS
    ]

    print(f"Rows read: {len(rows)}")
    print("Columns found: " + ", ".join(actual_columns))

    if missing_columns:
        print(
            "::error::Missing required columns: "
            + ", ".join(missing_columns)
        )
        return 1

    if unexpected_columns:
        print(
            "::warning::Unexpected columns found: "
            + ", ".join(unexpected_columns)
        )

    if not rows:
        print("::error::signals.csv contains no data rows.")
        return 1

    critical = Counter()
    warnings = Counter()
    source_counts = Counter()
    theme_counts = Counter()
    score_counts = Counter()
    examples = {}
    seen_ids = set()
    valid_rows = 0

    def add_issue(bucket, name, example):
        bucket[name] += 1
        examples.setdefault(name, []).append(example)

    for row_number, row in enumerate(rows, start=2):
        # csv.DictReader stores surplus fields under the None key.
        surplus = row.get(None)
        if surplus:
            add_issue(
                critical,
                "malformed_row_width",
                f"row {row_number}: {len(surplus)} surplus value(s)",
            )
            # Field positions are unreliable, so skip secondary checks.
            continue

        valid_rows += 1
        signal_id = safe_value(row, "signal_id")
        source = safe_value(row, "source")
        theme = safe_value(row, "discovery_theme")
        title = safe_value(row, "title")
        link = safe_value(row, "link")
        description = safe_value(row, "description")
        published_at = safe_value(row, "published_at")
        captured_at = safe_value(row, "captured_at")
        points = safe_value(row, "points")
        comments = safe_value(row, "comments")
        problem_language = safe_value(row, "problem_language").lower()
        purchase_language = safe_value(row, "purchase_language").lower()
        target_customer = safe_value(row, "target_customer_detected")
        recency_days = safe_value(row, "recency_days")
        source_count = safe_value(row, "source_count")
        duplicate_theme_count = safe_value(row, "duplicate_theme_count")
        relevance_score = safe_value(row, "relevance_score")

        if source:
            source_counts[source] += 1
        else:
            add_issue(critical, "missing_source", f"row {row_number}")

        if theme:
            theme_counts[theme] += 1
            if theme not in ALLOWED_DISCOVERY_THEMES:
                add_issue(
                    warnings,
                    "unknown_discovery_theme",
                    f"row {row_number}: {theme!r}",
                )
        else:
            add_issue(
                critical,
                "missing_discovery_theme",
                f"row {row_number}",
            )

        if not title:
            add_issue(critical, "missing_title", f"row {row_number}")

        if not SIGNAL_ID_PATTERN.fullmatch(signal_id):
            add_issue(
                critical,
                "invalid_signal_id",
                f"row {row_number}: {signal_id!r}",
            )
        elif signal_id in seen_ids:
            add_issue(
                critical,
                "duplicate_signal_id",
                f"row {row_number}: {signal_id}",
            )
        else:
            seen_ids.add(signal_id)

        if not is_valid_http_url(link):
            bucket = critical if STRICT_URLS else warnings
            add_issue(
                bucket,
                "invalid_link",
                f"row {row_number}: {link[:120]!r}",
            )

        if not description:
            add_issue(warnings, "blank_description", f"row {row_number}")
        elif HTML_PATTERN.search(description):
            add_issue(
                warnings,
                "html_in_description",
                f"row {row_number}: {description[:120]}",
            )

        if not is_valid_timestamp(published_at, allow_blank=True):
            add_issue(
                warnings,
                "invalid_published_at",
                f"row {row_number}: {published_at!r}",
            )

        if not is_valid_timestamp(captured_at, allow_blank=False):
            add_issue(
                critical,
                "invalid_captured_at",
                f"row {row_number}: {captured_at!r}",
            )

        if not is_nonnegative_integer(points, allow_blank=True):
            add_issue(
                warnings,
                "invalid_points",
                f"row {row_number}: {points!r}",
            )

        if not is_nonnegative_integer(comments, allow_blank=True):
            add_issue(
                warnings,
                "invalid_comments",
                f"row {row_number}: {comments!r}",
            )

        if problem_language not in BOOLEAN_VALUES:
            add_issue(
                warnings,
                "invalid_problem_language",
                f"row {row_number}: {problem_language!r}",
            )

        if purchase_language not in BOOLEAN_VALUES:
            add_issue(
                warnings,
                "invalid_purchase_language",
                f"row {row_number}: {purchase_language!r}",
            )

        if "\n" in target_customer or "\r" in target_customer:
            add_issue(
                warnings,
                "multiline_target_customer",
                f"row {row_number}",
            )
        if len(target_customer) > 300:
            add_issue(
                warnings,
                "long_target_customer",
                f"row {row_number}: {len(target_customer)} characters",
            )

        enrichment_values = {
            "recency_days": recency_days,
            "source_count": source_count,
            "duplicate_theme_count": duplicate_theme_count,
            "relevance_score": relevance_score,
        }
        for field, value in enrichment_values.items():
            if not is_nonnegative_integer(value, allow_blank=not STRICT_ENRICHMENT):
                bucket = critical if STRICT_ENRICHMENT else warnings
                add_issue(
                    bucket,
                    f"invalid_{field}",
                    f"row {row_number}: {value!r}",
                )

        if relevance_score.isdigit():
            score_counts[int(relevance_score)] += 1

    print("\nSummary:")
    print(f"  Total rows: {len(rows)}")
    print(f"  Structurally valid rows: {valid_rows}")
    print(
        "  Malformed row-width records: "
        f"{critical['malformed_row_width']}"
    )
    print(f"  Unique valid signal IDs: {len(seen_ids)}")

    print("\nSource counts (structurally valid rows):")
    for source, count in sorted(source_counts.items()):
        print(f"  {source}: {count}")

    print("\nDiscovery-theme counts (structurally valid rows):")
    for theme, count in sorted(theme_counts.items()):
        print(f"  {theme}: {count}")

    print("\nRelevance-score distribution:")
    if score_counts:
        for score, count in sorted(score_counts.items(), reverse=True):
            print(f"  {score}: {count}")
    else:
        print("  No valid scores found.")

    print("\nCritical issue counts:")
    if critical:
        for name, count in sorted(critical.items()):
            print(f"  {name}: {count}")
    else:
        print("  None")

    print("\nWarning counts:")
    if warnings:
        for name, count in sorted(warnings.items()):
            print(f"  {name}: {count}")
    else:
        print("  None")

    for name in sorted(examples):
        print_examples(name, examples[name])

    warning_total = sum(warnings.values())
    critical_total = sum(critical.values())

    if warning_total:
        print(
            f"\n::warning::QA detected {warning_total} "
            "transitional/non-critical issue(s)."
        )

    if critical_total:
        print(
            f"\n::error::QA failed with {critical_total} "
            "critical issue(s)."
        )
        return 1

    print("\nQA PASSED.")
    print(
        f"Validated {len(rows)} rows across "
        f"{len(source_counts)} source(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
