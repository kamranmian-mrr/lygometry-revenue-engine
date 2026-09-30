import csv
import hashlib
import math
import re
import sys
from collections import Counter, defaultdict
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
    "opportunity_id", "opportunity_title", "discovery_theme",
    "problem_statement", "target_customer", "topic_key", "signal_count",
    "independent_source_count", "latest_published_at", "median_recency_days",
    "problem_evidence_count", "purchase_evidence_count",
    "customer_evidence_count", "highest_signal_score", "average_signal_score",
    "evidence_links", "representative_signal_ids", "opportunity_score",
    "confidence_level", "status", "created_at", "updated_at",
]
MAP_COLUMNS = ["opportunity_id", "signal_id", "match_method", "match_score", "added_at"]
UNCLASSIFIED_COLUMNS = [
    "signal_id", "source", "discovery_theme", "title",
    "target_customer_detected", "relevance_score", "reason", "recorded_at",
]

HEX16 = re.compile(r"^[0-9a-f]{16}$")
ALLOWED_CONFIDENCE = {"low", "emerging", "supported"}
ALLOWED_STATUS = {
    "discovered", "monitor", "ready_for_review",
    "experiment_candidate", "rejected",
}
PROTECTED_STATUSES = {"experiment_candidate", "rejected"}
ALLOWED_MATCH_METHODS = {"theme+customer+keyword_v2"}
ALLOWED_UNCLASSIFIED_REASONS = {"no_secondary_topic_match"}

MAX_OPPORTUNITY_SCORE = 20
MAX_MATCH_SCORE = 100
MAX_EVIDENCE_LINKS = 5
MAX_REPRESENTATIVE_IDS = 10
GENERAL_SPLIT_MIN_SIZE = 4
STRICT_READY_RULES = True
STRICT_LARGE_GENERAL = True
STRICT_REFERENTIAL_INTEGRITY = True
STRICT_SINGLE_OPPORTUNITY_PER_SIGNAL = True


def clean(value):
    return "" if value is None else str(value).strip()


def expected_opportunity_id(theme, customer, topic):
    """Recreate the stable ID contract used by opportunities.py v3.1."""
    key = "|".join(
        [
            clean(theme).casefold(),
            clean(customer).casefold(),
            clean(topic).casefold(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def split_pipe_values(value):
    return [part.strip() for part in clean(value).split("|") if part.strip()]


def parse_integer(value, minimum=None, maximum=None):
    text = clean(value)
    if not re.fullmatch(r"-?\d+", text):
        return None
    try:
        number = int(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def parse_number(value, minimum=None, maximum=None):
    text = clean(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def parse_timestamp(value, allow_blank=False, require_timezone=False):
    text = clean(value)
    if not text:
        return None if allow_blank else False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if require_timezone and parsed.tzinfo is None:
        return False
    return parsed


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
        columns = reader.fieldnames or []
        duplicates = sorted(name for name, count in Counter(columns).items() if count > 1)
        missing = [name for name in required_columns if name not in columns]
        extra = [name for name in columns if name not in required_columns]
        rows = list(reader)
    return missing, extra, duplicates, rows


def print_examples(title, values, limit=5):
    if not values:
        return
    print(f"\n{title}:")
    for value in values[:limit]:
        print(f"  - {value}")
    if len(values) > limit:
        print(f"  ... and {len(values) - limit} more")


def main():
    print("=== Lygometry Opportunity QA v1.2 ===")
    print(f"Opportunities: {OPPORTUNITIES_FILE}")
    print(f"Mappings: {MAP_FILE}")
    print(f"Unclassified: {UNCLASSIFIED_FILE}")

    critical, warnings, examples = Counter(), Counter(), {}

    def add(bucket, name, example):
        bucket[name] += 1
        examples.setdefault(name, []).append(example)

    try:
        opp_missing, opp_extra, opp_dup_headers, opportunities = read_csv_file(OPPORTUNITIES_FILE, OPPORTUNITY_COLUMNS)
        map_missing, map_extra, map_dup_headers, mappings = read_csv_file(MAP_FILE, MAP_COLUMNS)
        unc_missing, unc_extra, unc_dup_headers, unclassified = read_csv_file(UNCLASSIFIED_FILE, UNCLASSIFIED_COLUMNS)
    except FileNotFoundError as error:
        print(f"::error::Required output file is missing: {error}")
        return 1
    except (OSError, csv.Error) as error:
        print(f"::error::Could not read opportunity outputs: {error}")
        return 1

    for label, missing, extra, duplicates in (
        ("opportunities", opp_missing, opp_extra, opp_dup_headers),
        ("mapping", map_missing, map_extra, map_dup_headers),
        ("unclassified", unc_missing, unc_extra, unc_dup_headers),
    ):
        if missing:
            add(critical, f"missing_{label}_columns", ", ".join(missing))
        if duplicates:
            add(critical, f"duplicate_{label}_headers", ", ".join(duplicates))
        if extra:
            add(warnings, f"unexpected_{label}_columns", ", ".join(extra))

    if any((opp_missing, map_missing, unc_missing, opp_dup_headers, map_dup_headers, unc_dup_headers)):
        for name in sorted(examples):
            print_examples(name, examples[name])
        print("\n::error::Opportunity QA stopped because output schemas are invalid.")
        return 1

    if not opportunities:
        add(critical, "no_opportunity_rows", str(OPPORTUNITIES_FILE))
    if opportunities and not mappings:
        add(critical, "opportunities_without_mappings", str(MAP_FILE))

    signal_ids = set()
    if not SIGNALS_FILE.exists():
        bucket = critical if STRICT_REFERENTIAL_INTEGRITY else warnings
        add(bucket, "signals_file_missing", str(SIGNALS_FILE))
    else:
        try:
            with SIGNALS_FILE.open("r", newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                headers = reader.fieldnames or []
                duplicate_headers = [key for key, count in Counter(headers).items() if count > 1]
                if duplicate_headers:
                    add(critical, "duplicate_signals_headers", ", ".join(duplicate_headers))
                if "signal_id" not in headers:
                    add(critical, "signals_missing_signal_id", str(SIGNALS_FILE))
                else:
                    for row_number, row in enumerate(reader, start=2):
                        if row.get(None):
                            add(critical, "malformed_signal_row", f"row {row_number}: {len(row.get(None) or [])} surplus value(s)")
                            continue
                        signal_id = clean(row.get("signal_id"))
                        if not HEX16.fullmatch(signal_id):
                            add(critical, "invalid_signal_id_in_signals", f"row {row_number}: {signal_id!r}")
                        elif signal_id in signal_ids:
                            add(critical, "duplicate_signal_id_in_signals", f"row {row_number}: {signal_id}")
                        else:
                            signal_ids.add(signal_id)
        except (OSError, csv.Error) as error:
            add(critical, "signals_file_unreadable", str(error))

    opportunity_ids = set()
    semantic_opportunity_ids = {}
    expected_mapping_counts = {}
    representatives_by_opportunity = {}
    created_by_opportunity = {}

    for row_number, row in enumerate(opportunities, start=2):
        if row.get(None):
            add(critical, "malformed_opportunity_row", f"row {row_number}")
            continue

        oid = clean(row.get("opportunity_id"))
        title = clean(row.get("opportunity_title"))
        theme = clean(row.get("discovery_theme"))
        problem = clean(row.get("problem_statement"))
        customer = clean(row.get("target_customer"))
        topic = clean(row.get("topic_key"))
        confidence = clean(row.get("confidence_level"))
        status = clean(row.get("status"))

        valid_opportunity_id = bool(HEX16.fullmatch(oid))
        unique_opportunity_id = valid_opportunity_id and oid not in opportunity_ids

        if not valid_opportunity_id:
            add(critical, "invalid_opportunity_id", f"row {row_number}: {oid!r}")
        elif not unique_opportunity_id:
            add(critical, "duplicate_opportunity_id", f"row {row_number}: {oid}")
        else:
            opportunity_ids.add(oid)

        if valid_opportunity_id:
            expected_id = expected_opportunity_id(theme, customer, topic)
            if oid != expected_id:
                add(
                    critical,
                    "opportunity_id_key_mismatch",
                    f"row {row_number}: actual={oid}, expected={expected_id}",
                )

        semantic_key = (
            theme.casefold(),
            customer.casefold(),
            topic.casefold(),
        )
        if all(semantic_key):
            prior_id = semantic_opportunity_ids.get(semantic_key)
            if prior_id and prior_id != oid:
                add(
                    critical,
                    "duplicate_semantic_opportunity",
                    f"row {row_number}: {semantic_key} -> {prior_id}, {oid}",
                )
            elif valid_opportunity_id:
                semantic_opportunity_ids[semantic_key] = oid

        for field, value in (
            ("opportunity_title", title), ("discovery_theme", theme),
            ("problem_statement", problem), ("target_customer", customer),
            ("topic_key", topic),
        ):
            if not value:
                add(critical, f"blank_{field}", f"row {row_number}")

        parsed = {}
        for field, maximum in {
            "signal_count": None,
            "independent_source_count": None,
            "median_recency_days": None,
            "problem_evidence_count": None,
            "purchase_evidence_count": None,
            "customer_evidence_count": None,
            "highest_signal_score": None,
            "opportunity_score": MAX_OPPORTUNITY_SCORE,
        }.items():
            value = parse_integer(row.get(field), minimum=0, maximum=maximum)
            if value is None:
                add(critical, f"invalid_{field}", f"row {row_number}: {clean(row.get(field))!r}")
            else:
                parsed[field] = value

        average = parse_number(row.get("average_signal_score"), minimum=0)
        if average is None:
            add(critical, "invalid_average_signal_score", f"row {row_number}: {clean(row.get('average_signal_score'))!r}")
        elif parsed.get("highest_signal_score") is not None and average > parsed["highest_signal_score"]:
            add(critical, "average_exceeds_highest_signal_score", f"row {row_number}")

        signal_count = parsed.get("signal_count")
        if signal_count is not None:
            if unique_opportunity_id:
                expected_mapping_counts[oid] = signal_count
            if signal_count < 2:
                add(critical, "cluster_below_minimum_size", f"row {row_number}: {signal_count}")
            for field in ("problem_evidence_count", "purchase_evidence_count", "customer_evidence_count"):
                if parsed.get(field, 0) > signal_count:
                    add(critical, f"{field}_exceeds_signal_count", f"row {row_number}")
        if signal_count is not None and parsed.get("independent_source_count", 0) > signal_count:
            add(critical, "independent_sources_exceed_signals", f"row {row_number}")

        if confidence not in ALLOWED_CONFIDENCE:
            add(critical, "invalid_confidence", f"row {row_number}: {confidence!r}")
        if status not in ALLOWED_STATUS:
            add(critical, "invalid_status", f"row {row_number}: {status!r}")

        created = parse_timestamp(row.get("created_at"), require_timezone=True)
        updated = parse_timestamp(row.get("updated_at"), require_timezone=True)
        latest = parse_timestamp(row.get("latest_published_at"), allow_blank=True)
        if created is False:
            add(critical, "invalid_created_at", f"row {row_number}")
        if updated is False:
            add(critical, "invalid_updated_at", f"row {row_number}")
        if latest is False:
            add(warnings, "invalid_latest_published_at", f"row {row_number}")
        if isinstance(created, datetime) and isinstance(updated, datetime):
            if unique_opportunity_id:
                created_by_opportunity[oid] = created
            if created > updated:
                add(critical, "created_after_updated", f"row {row_number}")
        if isinstance(latest, datetime) and isinstance(updated, datetime):
            try:
                if latest > updated:
                    add(warnings, "publication_after_opportunity_update", f"row {row_number}")
            except TypeError:
                add(warnings, "timestamp_timezone_mismatch", f"row {row_number}")

        evidence_links = split_pipe_values(row.get("evidence_links"))
        if len(evidence_links) > MAX_EVIDENCE_LINKS:
            add(warnings, "too_many_evidence_links", f"row {row_number}: {len(evidence_links)}")
        for link in evidence_links:
            if not is_http_url(link):
                add(critical, "invalid_evidence_link", f"row {row_number}: {link!r}")

        representative_ids = split_pipe_values(row.get("representative_signal_ids"))
        if unique_opportunity_id:
            representatives_by_opportunity[oid] = representative_ids
        if not representative_ids:
            bucket = critical if status in {"ready_for_review", "experiment_candidate"} else warnings
            add(bucket, "no_representative_signal_ids", f"row {row_number}: {oid}")
        if len(representative_ids) > MAX_REPRESENTATIVE_IDS:
            add(warnings, "too_many_representative_ids", f"row {row_number}: {len(representative_ids)}")
        if len(set(representative_ids)) != len(representative_ids):
            add(critical, "duplicate_representative_signal_id", f"row {row_number}")
        for signal_id in representative_ids:
            if not HEX16.fullmatch(signal_id):
                add(critical, "invalid_representative_signal_id", f"row {row_number}: {signal_id!r}")
            elif signal_ids and signal_id not in signal_ids:
                add(critical, "representative_signal_missing_from_signals", f"row {row_number}: {signal_id}")

        if status in {"ready_for_review", "experiment_candidate"} and not evidence_links:
            add(critical, "advanced_candidate_without_evidence_link", f"row {row_number}: {oid}")
        elif not evidence_links:
            add(warnings, "opportunity_without_evidence_link", f"row {row_number}: {oid}")

        if STRICT_LARGE_GENERAL and topic == "general" and (signal_count or 0) >= GENERAL_SPLIT_MIN_SIZE:
            add(critical, "large_general_cluster_published", f"row {row_number}: {oid}")

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

        if status == "experiment_candidate":
            if topic == "general":
                add(warnings, "experiment_candidate_general_topic", f"row {row_number}")
            if customer == "General business":
                add(warnings, "experiment_candidate_general_customer", f"row {row_number}")
            if parsed.get("customer_evidence_count", 0) < 1:
                add(warnings, "experiment_candidate_without_customer_evidence", f"row {row_number}")
            if parsed.get("median_recency_days", 999999) > 180:
                add(warnings, "experiment_candidate_stale", f"row {row_number}")
            if parsed.get("opportunity_score", 0) < 13:
                add(warnings, "experiment_candidate_below_review_score", f"row {row_number}")
            if confidence == "low":
                add(warnings, "experiment_candidate_low_confidence", f"row {row_number}")

    mapping_pairs = set()
    mapped_count_by_opportunity = Counter()
    mapped_opportunity_by_signal = {}
    mapped_signal_ids = set()

    for row_number, row in enumerate(mappings, start=2):
        if row.get(None):
            add(critical, "malformed_mapping_row", f"row {row_number}")
            continue
        oid = clean(row.get("opportunity_id"))
        signal_id = clean(row.get("signal_id"))
        method = clean(row.get("match_method"))
        pair = (oid, signal_id)

        is_new_pair = pair not in mapping_pairs
        if not is_new_pair:
            add(critical, "duplicate_mapping_pair", f"row {row_number}: {pair}")
        else:
            mapping_pairs.add(pair)

        if oid not in opportunity_ids:
            add(critical if STRICT_REFERENTIAL_INTEGRITY else warnings, "mapping_unknown_opportunity", f"row {row_number}: {oid}")
        elif is_new_pair:
            mapped_count_by_opportunity[oid] += 1

        if not HEX16.fullmatch(signal_id):
            add(critical, "invalid_mapping_signal_id", f"row {row_number}: {signal_id!r}")
        elif signal_ids and signal_id not in signal_ids:
            add(critical if STRICT_REFERENTIAL_INTEGRITY else warnings, "mapping_signal_missing_from_signals", f"row {row_number}: {signal_id}")

        prior = mapped_opportunity_by_signal.get(signal_id)
        if STRICT_SINGLE_OPPORTUNITY_PER_SIGNAL and prior and prior != oid:
            add(critical, "signal_mapped_to_multiple_opportunities", f"{signal_id}: {prior}, {oid}")
        elif signal_id and is_new_pair:
            mapped_opportunity_by_signal[signal_id] = oid
        if signal_id:
            mapped_signal_ids.add(signal_id)

        if method not in ALLOWED_MATCH_METHODS:
            add(warnings, "unexpected_match_method", f"row {row_number}: {method!r}")
        if parse_integer(row.get("match_score"), minimum=0, maximum=MAX_MATCH_SCORE) is None:
            add(critical, "invalid_match_score", f"row {row_number}: {clean(row.get('match_score'))!r}")
        added = parse_timestamp(row.get("added_at"), require_timezone=True)
        if added is False:
            add(critical, "invalid_mapping_added_at", f"row {row_number}")
        elif isinstance(added, datetime) and isinstance(created_by_opportunity.get(oid), datetime):
            try:
                if added < created_by_opportunity[oid]:
                    add(warnings, "mapping_added_before_opportunity_created", f"row {row_number}: {oid}")
            except TypeError:
                add(warnings, "mapping_timestamp_timezone_mismatch", f"row {row_number}")

    for oid, expected in expected_mapping_counts.items():
        actual = mapped_count_by_opportunity.get(oid, 0)
        if actual != expected:
            add(critical, "signal_count_mapping_mismatch", f"{oid}: opportunity={expected}, mapping={actual}")

    for oid, representative_ids in representatives_by_opportunity.items():
        for signal_id in representative_ids:
            if (oid, signal_id) not in mapping_pairs:
                add(critical, "representative_not_mapped_to_opportunity", f"{oid}: {signal_id}")

    seen_unclassified = set()
    for row_number, row in enumerate(unclassified, start=2):
        if row.get(None):
            add(critical, "malformed_unclassified_row", f"row {row_number}")
            continue
        signal_id = clean(row.get("signal_id"))
        reason = clean(row.get("reason"))
        if not HEX16.fullmatch(signal_id):
            add(critical, "invalid_unclassified_signal_id", f"row {row_number}: {signal_id!r}")
        elif signal_id in seen_unclassified:
            add(critical, "duplicate_unclassified_signal_id", f"row {row_number}: {signal_id}")
        else:
            seen_unclassified.add(signal_id)
        if signal_ids and signal_id not in signal_ids:
            add(critical if STRICT_REFERENTIAL_INTEGRITY else warnings, "unclassified_signal_missing_from_signals", f"row {row_number}: {signal_id}")
        if signal_id in mapped_signal_ids:
            add(critical, "signal_both_mapped_and_unclassified", f"row {row_number}: {signal_id}")
        if reason not in ALLOWED_UNCLASSIFIED_REASONS:
            add(warnings, "unexpected_unclassified_reason", f"row {row_number}: {reason!r}")
        for field in ("source", "discovery_theme", "title"):
            if not clean(row.get(field)):
                add(critical, f"blank_unclassified_{field}", f"row {row_number}")
        if parse_integer(row.get("relevance_score"), minimum=0) is None:
            add(critical, "invalid_unclassified_relevance_score", f"row {row_number}")
        if parse_timestamp(row.get("recorded_at"), require_timezone=True) is False:
            add(critical, "invalid_unclassified_recorded_at", f"row {row_number}")

    print("\nPortfolio summary:")
    print(f"  Opportunities: {len(opportunities)}")
    print(f"  Mapping rows: {len(mappings)}")
    print(f"  Quarantined signals: {len(unclassified)}")
    print(f"  Signals available for cross-check: {len(signal_ids)}")

    print("\nStatus counts:")
    for status, count in sorted(Counter(clean(row.get("status")) for row in opportunities).items()):
        print(f"  {status or '(blank)'}: {count}")
    if not opportunities:
        print("  None")

    print("\nConfidence counts:")
    for confidence, count in sorted(Counter(clean(row.get("confidence_level")) for row in opportunities).items()):
        print(f"  {confidence or '(blank)'}: {count}")
    if not opportunities:
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
