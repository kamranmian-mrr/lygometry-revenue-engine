import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SIGNALS_FILE = DATA_DIR / "signals.csv"
OPPORTUNITIES_FILE = DATA_DIR / "opportunities.csv"
MAP_FILE = DATA_DIR / "opportunity_signal_map.csv"
UNCLASSIFIED_FILE = DATA_DIR / "unclassified_signals.csv"

OPPORTUNITY_COLUMNS = [
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

MAP_COLUMNS = [
    "opportunity_id",
    "signal_id",
    "match_method",
    "match_score",
    "added_at",
]

UNCLASSIFIED_COLUMNS = [
    "signal_id",
    "source",
    "discovery_theme",
    "title",
    "target_customer_detected",
    "relevance_score",
    "reason",
    "recorded_at",
]

SIGNAL_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
OPPORTUNITY_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

ALLOWED_CONFIDENCE = {"low", "emerging", "supported"}
ALLOWED_STATUS = {
    "discovered",
    "monitor",
    "ready_for_review",
    "experiment_candidate",
    "rejected",
}
ALLOWED_MATCH_METHODS = {"theme+customer+keyword_v2"}
ALLOWED_UNCLASSIFIED_REASONS = {"no_secondary_topic_match"}
PROTECTED_STATUSES = {"experiment_candidate", "rejected"}

MAX_OPPORTUNITY_SCORE = 20
MAX_MATCH_SCORE = 100
GENERAL_SPLIT_MIN_SIZE = 4
MAX_EVIDENCE_LINKS = 5
MAX_REPRESENTATIVE_IDS = 10

STRICT_READY_RULES = True
STRICT_LARGE_GENERAL = True
STRICT_REFERENTIAL_INTEGRITY = True


def clean(value):
    return "" if value is None else str(value).strip()


def split_pipe_values(value):
    return [part.strip() for part in clean(value).split("|") if part.strip()]


def is_integer(value, minimum=None, maximum=None):
    text = clean(value)
    if not re.fullmatch(r"-?\d+", text):
        return False
    number = int(text)
    if minimum is not None and number < minimum:
        return False
    if maximum is not None and number > maximum:
        return False
    return True


def is_number(value, minimum=None, maximum=None):
    text = clean(value)
    if not text:
        return False
    try:
        number = float(text)
    except ValueError:
        return False
    if number != number or number in {float("inf"), float("-inf")}:
        return False
    if minimum is not None and number < minimum:
        return False
    if maximum is not None and number > maximum:
        return False
    return True


def is_timestamp(value, allow_blank=False):
    text = clean(value)
    if not text:
        return allow_blank
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def is_http_url(value):
    try:
        parsed = urlsplit(clean(value))
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def read_csv_file(path, required_columns):
    if not path.exists():
        raise FileNotFoundError(str(path))

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        actual_columns = reader.fieldnames or []
        missing = [column for column in required_columns if column not in actual_columns]
        extra = [column for column in actual_columns if column not in required_columns]
        rows = list(reader)

    return actual_columns, missing, extra, rows


def print_examples(title, values, limit=5):
    if not values:
        return
    print(f"\n{title}:")
    for value in values[:limit]:
        print(f"  - {value}")
    if len(values) > limit:
        print(f"  ... and {len(values) - limit} more")


def main():
    print("=== Lygometry Opportunity QA v1 ===")
    print(f"Opportunities: {OPPORTUNITIES_FILE}")
    print(f"Mappings: {MAP_FILE}")
    print(f"Unclassified: {UNCLASSIFIED_FILE}")

    critical = Counter()
    warnings = Counter()
    examples = {}

    def add(bucket, name, example):
        bucket[name] += 1
        examples.setdefault(name, []).append(example)

    try:
        _, opp_missing, opp_extra, opportunities = read_csv_file(
            OPPORTUNITIES_FILE, OPPORTUNITY_COLUMNS
        )
        _, map_missing, map_extra, mappings = read_csv_file(MAP_FILE, MAP_COLUMNS)
        _, unc_missing, unc_extra, unclassified = read_csv_file(
            UNCLASSIFIED_FILE, UNCLASSIFIED_COLUMNS
        )
    except FileNotFoundError as error:
        print(f"::error::Required output file is missing: {error}")
        return 1
    except (OSError, csv.Error) as error:
        print(f"::error::Could not read opportunity outputs: {error}")
        return 1

    for label, missing in (
        ("opportunities", opp_missing),
        ("mapping", map_missing),
        ("unclassified", unc_missing),
    ):
        if missing:
            add(critical, f"missing_{label}_columns", ", ".join(missing))

    for label, extra in (
        ("opportunities", opp_extra),
        ("mapping", map_extra),
        ("unclassified", unc_extra),
    ):
        if extra:
            add(warnings, f"unexpected_{label}_columns", ", ".join(extra))

    if any((opp_missing, map_missing, unc_missing)):
        for name in sorted(examples):
            print_examples(name, examples[name])
        print("\n::error::Opportunity QA stopped because required columns are missing.")
        return 1

    signal_ids = set()
    if SIGNALS_FILE.exists():
        try:
            with SIGNALS_FILE.open("r", newline="", encoding="utf-8-sig") as handle:
                signal_reader = csv.DictReader(handle)
                if "signal_id" not in (signal_reader.fieldnames or []):
                    add(critical, "signals_missing_signal_id", str(SIGNALS_FILE))
                else:
                    for row in signal_reader:
                        if row.get(None):
                            continue
                        signal_id = clean(row.get("signal_id"))
                        if signal_id:
                            signal_ids.add(signal_id)
        except (OSError, csv.Error) as error:
            add(critical, "signals_file_unreadable", str(error))
    else:
        add(warnings, "signals_file_missing", str(SIGNALS_FILE))

    opportunity_ids = set()
    opportunity_by_id = {}
    expected_mapping_counts = {}
    mapped_signal_ids_by_opportunity = Counter()

    for row_number, row in enumerate(opportunities, start=2):
        if row.get(None):
            add(critical, "malformed_opportunity_row", f"row {row_number}")
            continue

        opportunity_id = clean(row.get("opportunity_id"))
        title = clean(row.get("opportunity_title"))
        theme = clean(row.get("discovery_theme"))
        problem = clean(row.get("problem_statement"))
        customer = clean(row.get("target_customer"))
        topic = clean(row.get("topic_key"))
        confidence = clean(row.get("confidence_level"))
        status = clean(row.get("status"))

        if not OPPORTUNITY_ID_PATTERN.fullmatch(opportunity_id):
            add(critical, "invalid_opportunity_id", f"row {row_number}: {opportunity_id!r}")
        elif opportunity_id in opportunity_ids:
            add(critical, "duplicate_opportunity_id", f"row {row_number}: {opportunity_id}")
        else:
            opportunity_ids.add(opportunity_id)
            opportunity_by_id[opportunity_id] = row

        for field, value in (
            ("opportunity_title", title),
            ("discovery_theme", theme),
            ("problem_statement", problem),
            ("target_customer", customer),
            ("topic_key", topic),
        ):
            if not value:
                add(critical, f"blank_{field}", f"row {row_number}")

        integer_fields = {
            "signal_count": None,
            "independent_source_count": None,
            "median_recency_days": None,
            "problem_evidence_count": None,
            "purchase_evidence_count": None,
            "customer_evidence_count": None,
            "highest_signal_score": None,
            "opportunity_score": MAX_OPPORTUNITY_SCORE,
        }
        parsed = {}
        for field, maximum in integer_fields.items():
            value = clean(row.get(field))
            if not is_integer(value, minimum=0, maximum=maximum):
                add(critical, f"invalid_{field}", f"row {row_number}: {value!r}")
            else:
                parsed[field] = int(value)

        average_score = clean(row.get("average_signal_score"))
        if not is_number(average_score, minimum=0):
            add(critical, "invalid_average_signal_score", f"row {row_number}: {average_score!r}")

        signal_count = parsed.get("signal_count")
        independent_count = parsed.get("independent_source_count")
        if signal_count is not None:
            if signal_count < 2:
                add(critical, "cluster_below_minimum_size", f"row {row_number}: {signal_count}")
            expected_mapping_counts[opportunity_id] = signal_count
            for field in (
                "problem_evidence_count",
                "purchase_evidence_count",
                "customer_evidence_count",
            ):
                if parsed.get(field, 0) > signal_count:
                    add(critical, f"{field}_exceeds_signal_count", f"row {row_number}")
        if signal_count is not None and independent_count is not None and independent_count > signal_count:
            add(critical, "independent_sources_exceed_signals", f"row {row_number}")

        if confidence not in ALLOWED_CONFIDENCE:
            add(critical, "invalid_confidence", f"row {row_number}: {confidence!r}")
        if status not in ALLOWED_STATUS:
            add(critical, "invalid_status", f"row {row_number}: {status!r}")

        for field in ("created_at", "updated_at"):
            if not is_timestamp(row.get(field), allow_blank=False):
                add(critical, f"invalid_{field}", f"row {row_number}: {clean(row.get(field))!r}")
        if not is_timestamp(row.get("latest_published_at"), allow_blank=True):
            add(warnings, "invalid_latest_published_at", f"row {row_number}")

        evidence_links = split_pipe_values(row.get("evidence_links"))
        if len(evidence_links) > MAX_EVIDENCE_LINKS:
            add(warnings, "too_many_evidence_links", f"row {row_number}: {len(evidence_links)}")
        for link in evidence_links:
            if not is_http_url(link):
                add(critical, "invalid_evidence_link", f"row {row_number}: {link!r}")

        representative_ids = split_pipe_values(row.get("representative_signal_ids"))
        if len(representative_ids) > MAX_REPRESENTATIVE_IDS:
            add(warnings, "too_many_representative_ids", f"row {row_number}: {len(representative_ids)}")
        if len(set(representative_ids)) != len(representative_ids):
            add(critical, "duplicate_representative_signal_id", f"row {row_number}")
        for signal_id in representative_ids:
            if not SIGNAL_ID_PATTERN.fullmatch(signal_id):
                add(critical, "invalid_representative_signal_id", f"row {row_number}: {signal_id!r}")
            elif signal_ids and signal_id not in signal_ids:
                add(critical, "representative_signal_missing_from_signals", f"row {row_number}: {signal_id}")

        if STRICT_LARGE_GENERAL and topic == "general" and (signal_count or 0) >= GENERAL_SPLIT_MIN_SIZE:
            add(critical, "large_general_cluster_published", f"row {row_number}: {opportunity_id}")

        if STRICT_READY_RULES and status == "ready_for_review":
            if topic == "general":
                add(critical, "ready_general_topic", f"row {row_number}")
            if customer == "General business":
                add(critical, "ready_general_customer", f"row {row_number}")
            if parsed.get("customer_evidence_count", 0) < 1:
                add(critical, "ready_without_customer_evidence", f"row {row_number}")
            if parsed.get("median_recency_days", 999999) > 180:
                add(critical, "ready_with_stale_evidence", f"row {row_number}")
            if parsed.get("opportunity_score", 0) < 13:
                add(critical, "ready_below_score_threshold", f"row {row_number}")
            if confidence not in {"emerging", "supported"}:
                add(critical, "ready_with_low_confidence", f"row {row_number}")

    seen_mapping_pairs = set()
    mapped_signal_ids = set()
    for row_number, row in enumerate(mappings, start=2):
        if row.get(None):
            add(critical, "malformed_mapping_row", f"row {row_number}")
            continue

        opportunity_id = clean(row.get("opportunity_id"))
        signal_id = clean(row.get("signal_id"))
        method = clean(row.get("match_method"))
        pair = (opportunity_id, signal_id)

        if pair in seen_mapping_pairs:
            add(critical, "duplicate_mapping_pair", f"row {row_number}: {pair}")
        else:
            seen_mapping_pairs.add(pair)

        if opportunity_id not in opportunity_ids:
            bucket = critical if STRICT_REFERENTIAL_INTEGRITY else warnings
            add(bucket, "mapping_unknown_opportunity", f"row {row_number}: {opportunity_id}")
        else:
            mapped_signal_ids_by_opportunity[opportunity_id] += 1

        if not SIGNAL_ID_PATTERN.fullmatch(signal_id):
            add(critical, "invalid_mapping_signal_id", f"row {row_number}: {signal_id!r}")
        elif signal_ids and signal_id not in signal_ids:
            bucket = critical if STRICT_REFERENTIAL_INTEGRITY else warnings
            add(bucket, "mapping_signal_missing_from_signals", f"row {row_number}: {signal_id}")

        mapped_signal_ids.add(signal_id)

        if method not in ALLOWED_MATCH_METHODS:
            add(warnings, "unexpected_match_method", f"row {row_number}: {method!r}")
        if not is_integer(row.get("match_score"), minimum=0, maximum=MAX_MATCH_SCORE):
            add(critical, "invalid_match_score", f"row {row_number}: {clean(row.get('match_score'))!r}")
        if not is_timestamp(row.get("added_at"), allow_blank=False):
            add(critical, "invalid_mapping_added_at", f"row {row_number}")

    for opportunity_id, expected_count in expected_mapping_counts.items():
        actual_count = mapped_signal_ids_by_opportunity.get(opportunity_id, 0)
        if actual_count != expected_count:
            add(
                critical,
                "signal_count_mapping_mismatch",
                f"{opportunity_id}: opportunity={expected_count}, mapping={actual_count}",
            )

    seen_unclassified_ids = set()
    for row_number, row in enumerate(unclassified, start=2):
        if row.get(None):
            add(critical, "malformed_unclassified_row", f"row {row_number}")
            continue

        signal_id = clean(row.get("signal_id"))
        reason = clean(row.get("reason"))

        if not SIGNAL_ID_PATTERN.fullmatch(signal_id):
            add(critical, "invalid_unclassified_signal_id", f"row {row_number}: {signal_id!r}")
        elif signal_id in seen_unclassified_ids:
            add(critical, "duplicate_unclassified_signal_id", f"row {row_number}: {signal_id}")
        else:
            seen_unclassified_ids.add(signal_id)

        if signal_ids and signal_id not in signal_ids:
            bucket = critical if STRICT_REFERENTIAL_INTEGRITY else warnings
            add(bucket, "unclassified_signal_missing_from_signals", f"row {row_number}: {signal_id}")
        if signal_id in mapped_signal_ids:
            add(critical, "signal_both_mapped_and_unclassified", f"row {row_number}: {signal_id}")
        if reason not in ALLOWED_UNCLASSIFIED_REASONS:
            add(warnings, "unexpected_unclassified_reason", f"row {row_number}: {reason!r}")
        if not clean(row.get("source")):
            add(critical, "blank_unclassified_source", f"row {row_number}")
        if not clean(row.get("discovery_theme")):
            add(critical, "blank_unclassified_theme", f"row {row_number}")
        if not clean(row.get("title")):
            add(critical, "blank_unclassified_title", f"row {row_number}")
        if not is_integer(row.get("relevance_score"), minimum=0):
            add(critical, "invalid_unclassified_relevance_score", f"row {row_number}")
        if not is_timestamp(row.get("recorded_at"), allow_blank=False):
            add(critical, "invalid_unclassified_recorded_at", f"row {row_number}")

    print("\nPortfolio summary:")
    print(f"  Opportunities: {len(opportunities)}")
    print(f"  Mapping rows: {len(mappings)}")
    print(f"  Quarantined signals: {len(unclassified)}")
    print(f"  Signals available for cross-check: {len(signal_ids)}")

    print("\nStatus counts:")
    status_counts = Counter(clean(row.get("status")) for row in opportunities)
    if status_counts:
        for status, count in sorted(status_counts.items()):
            print(f"  {status or '(blank)'}: {count}")
    else:
        print("  None")

    print("\nConfidence counts:")
    confidence_counts = Counter(clean(row.get("confidence_level")) for row in opportunities)
    if confidence_counts:
        for confidence, count in sorted(confidence_counts.items()):
            print(f"  {confidence or '(blank)'}: {count}")
    else:
        print("  None")

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
        print(f"\n::warning::Opportunity QA detected {warning_total} warning(s).")

    if critical_total:
        print(f"\n::error::Opportunity QA failed with {critical_total} critical issue(s).")
        return 1

    print("\nOPPORTUNITY QA PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
