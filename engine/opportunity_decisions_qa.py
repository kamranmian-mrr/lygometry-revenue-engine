import csv
import hashlib
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DECISIONS_FILE = DATA_DIR / "opportunity_decisions.csv"
OPPORTUNITIES_FILE = DATA_DIR / "opportunities.csv"
EXPERIMENTS_FILE = DATA_DIR / "experiments.csv"

DECISION_COLUMNS = [
    "decision_id", "opportunity_id", "decision", "decision_reason",
    "reviewed_by", "reviewed_at", "next_action", "next_review_at",
    "experiment_id", "merged_into_opportunity_id", "opportunity_score_at_review",
    "confidence_at_review", "target_customer_at_review", "topic_key_at_review",
    "representative_signal_ids_at_review", "supersedes_decision_id", "is_active",
    "created_at", "updated_at",
]

HEX16 = re.compile(r"^[0-9a-f]{16}$")
ALLOWED_DECISIONS = {
    "monitor", "ready_for_review", "experiment_candidate",
    "rejected", "merged", "paused",
}
ALLOWED_CONFIDENCE = {"low", "emerging", "supported"}
ALLOWED_ACTIVE = {"yes", "no"}
MAX_OPPORTUNITY_SCORE = 20
MAX_REPRESENTATIVE_IDS = 10
STRICT_CURRENT_OPPORTUNITY_REFERENCE = False
STRICT_EXPERIMENT_REFERENCE = False


def clean(value):
    return "" if value is None else str(value).strip()


def parse_timestamp(value, allow_blank=False, require_timezone=True):
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


def parse_integer(value, minimum=None, maximum=None, allow_blank=False):
    text = clean(value)
    if not text:
        return None if allow_blank else False
    if not re.fullmatch(r"-?\d+", text):
        return False
    try:
        number = int(text)
    except (TypeError, ValueError, OverflowError):
        return False
    if minimum is not None and number < minimum:
        return False
    if maximum is not None and number > maximum:
        return False
    return number


def split_pipe_values(value):
    return [part.strip() for part in clean(value).split("|") if part.strip()]


def expected_decision_id(opportunity_id, reviewed_at, decision):
    key = "|".join([
        clean(opportunity_id).casefold(),
        clean(reviewed_at).casefold(),
        clean(decision).casefold(),
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def print_examples(title, values, limit=5):
    if not values:
        return
    print(f"\n{title}:")
    for value in values[:limit]:
        print(f"  - {value}")
    if len(values) > limit:
        print(f"  ... and {len(values) - limit} more")


def main():
    print("=== Lygometry Opportunity Decisions QA v1 ===")
    print(f"Decisions: {DECISIONS_FILE}")

    critical = Counter()
    warnings = Counter()
    examples = {}

    def add(bucket, name, example):
        bucket[name] += 1
        examples.setdefault(name, []).append(example)

    try:
        headers, rows = read_csv(DECISIONS_FILE)
    except FileNotFoundError as error:
        print(f"::error::Decision register is missing: {error}")
        return 1
    except (OSError, csv.Error) as error:
        print(f"::error::Could not read decision register: {error}")
        return 1

    duplicate_headers = sorted(name for name, count in Counter(headers).items() if count > 1)
    missing_headers = [name for name in DECISION_COLUMNS if name not in headers]
    extra_headers = [name for name in headers if name not in DECISION_COLUMNS]

    if duplicate_headers:
        add(critical, "duplicate_headers", ", ".join(duplicate_headers))
    if missing_headers:
        add(critical, "missing_columns", ", ".join(missing_headers))
    if extra_headers:
        add(warnings, "unexpected_columns", ", ".join(extra_headers))
    if duplicate_headers or missing_headers:
        for name in sorted(examples):
            print_examples(name, examples[name])
        print("\n::error::Decision QA stopped because the schema is invalid.")
        return 1

    # A header-only register is valid before the first human decision.
    if not rows:
        print("\nDecision register contains no decisions yet.")
        print("DECISION QA PASSED.")
        return 0

    current_opportunity_ids = set()
    if OPPORTUNITIES_FILE.exists():
        try:
            opp_headers, opp_rows = read_csv(OPPORTUNITIES_FILE)
            if "opportunity_id" not in opp_headers:
                add(warnings, "opportunities_missing_id_column", str(OPPORTUNITIES_FILE))
            else:
                current_opportunity_ids = {
                    clean(row.get("opportunity_id"))
                    for row in opp_rows
                    if HEX16.fullmatch(clean(row.get("opportunity_id")))
                }
        except (OSError, csv.Error) as error:
            add(warnings, "opportunities_file_unreadable", str(error))
    else:
        add(warnings, "opportunities_file_missing", str(OPPORTUNITIES_FILE))

    experiment_ids = set()
    if EXPERIMENTS_FILE.exists():
        try:
            experiment_headers, experiment_rows = read_csv(EXPERIMENTS_FILE)
            if "experiment_id" not in experiment_headers:
                add(warnings, "experiments_missing_id_column", str(EXPERIMENTS_FILE))
            else:
                experiment_ids = {
                    clean(row.get("experiment_id"))
                    for row in experiment_rows
                    if clean(row.get("experiment_id"))
                }
        except (OSError, csv.Error) as error:
            add(warnings, "experiments_file_unreadable", str(error))

    decision_ids = set()
    decision_by_id = {}
    active_by_opportunity = defaultdict(list)
    supersedes_edges = {}

    for row_number, row in enumerate(rows, start=2):
        if row.get(None):
            add(critical, "malformed_decision_row", f"row {row_number}: {len(row.get(None) or [])} surplus value(s)")
            continue

        decision_id = clean(row.get("decision_id"))
        opportunity_id = clean(row.get("opportunity_id"))
        decision = clean(row.get("decision"))
        reason = clean(row.get("decision_reason"))
        reviewer = clean(row.get("reviewed_by"))
        reviewed_at_text = clean(row.get("reviewed_at"))
        next_action = clean(row.get("next_action"))
        next_review_text = clean(row.get("next_review_at"))
        experiment_id = clean(row.get("experiment_id"))
        merged_into = clean(row.get("merged_into_opportunity_id"))
        confidence = clean(row.get("confidence_at_review"))
        target_customer = clean(row.get("target_customer_at_review"))
        topic_key = clean(row.get("topic_key_at_review"))
        supersedes = clean(row.get("supersedes_decision_id"))
        is_active = clean(row.get("is_active")).casefold()

        valid_decision_id = bool(HEX16.fullmatch(decision_id))
        if not valid_decision_id:
            add(critical, "invalid_decision_id", f"row {row_number}: {decision_id!r}")
        elif decision_id in decision_ids:
            add(critical, "duplicate_decision_id", f"row {row_number}: {decision_id}")
        else:
            decision_ids.add(decision_id)
            decision_by_id[decision_id] = row

        if not HEX16.fullmatch(opportunity_id):
            add(critical, "invalid_opportunity_id", f"row {row_number}: {opportunity_id!r}")
        elif current_opportunity_ids and opportunity_id not in current_opportunity_ids:
            bucket = critical if STRICT_CURRENT_OPPORTUNITY_REFERENCE and is_active == "yes" else warnings
            add(bucket, "opportunity_not_in_current_portfolio", f"row {row_number}: {opportunity_id}")

        if decision not in ALLOWED_DECISIONS:
            add(critical, "invalid_decision", f"row {row_number}: {decision!r}")
        if not reason:
            add(critical, "blank_decision_reason", f"row {row_number}")
        if not reviewer:
            add(critical, "blank_reviewed_by", f"row {row_number}")
        if not next_action:
            add(warnings, "blank_next_action", f"row {row_number}")

        reviewed_at = parse_timestamp(reviewed_at_text)
        next_review_at = parse_timestamp(next_review_text, allow_blank=True)
        created_at = parse_timestamp(row.get("created_at"))
        updated_at = parse_timestamp(row.get("updated_at"))

        if reviewed_at is False:
            add(critical, "invalid_reviewed_at", f"row {row_number}: {reviewed_at_text!r}")
        if next_review_at is False:
            add(critical, "invalid_next_review_at", f"row {row_number}: {next_review_text!r}")
        if created_at is False:
            add(critical, "invalid_created_at", f"row {row_number}")
        if updated_at is False:
            add(critical, "invalid_updated_at", f"row {row_number}")

        if isinstance(created_at, datetime) and isinstance(updated_at, datetime) and created_at > updated_at:
            add(critical, "created_after_updated", f"row {row_number}")
        if isinstance(reviewed_at, datetime) and isinstance(created_at, datetime) and reviewed_at > created_at:
            add(warnings, "reviewed_after_created", f"row {row_number}")
        if isinstance(next_review_at, datetime) and isinstance(reviewed_at, datetime) and next_review_at <= reviewed_at:
            add(warnings, "next_review_not_after_review", f"row {row_number}")

        if valid_decision_id and reviewed_at is not False and decision in ALLOWED_DECISIONS:
            expected_id = expected_decision_id(opportunity_id, reviewed_at_text, decision)
            if decision_id != expected_id:
                add(critical, "decision_id_key_mismatch", f"row {row_number}: actual={decision_id}, expected={expected_id}")

        score = parse_integer(row.get("opportunity_score_at_review"), minimum=0, maximum=MAX_OPPORTUNITY_SCORE, allow_blank=True)
        if score is False:
            add(critical, "invalid_opportunity_score_at_review", f"row {row_number}: {clean(row.get('opportunity_score_at_review'))!r}")
        if confidence and confidence not in ALLOWED_CONFIDENCE:
            add(critical, "invalid_confidence_at_review", f"row {row_number}: {confidence!r}")
        if not target_customer:
            add(warnings, "blank_target_customer_snapshot", f"row {row_number}")
        if not topic_key:
            add(warnings, "blank_topic_key_snapshot", f"row {row_number}")

        representative_ids = split_pipe_values(row.get("representative_signal_ids_at_review"))
        if len(representative_ids) > MAX_REPRESENTATIVE_IDS:
            add(warnings, "too_many_representative_signal_ids", f"row {row_number}: {len(representative_ids)}")
        if len(representative_ids) != len(set(representative_ids)):
            add(critical, "duplicate_representative_signal_id", f"row {row_number}")
        for signal_id in representative_ids:
            if not HEX16.fullmatch(signal_id):
                add(critical, "invalid_representative_signal_id", f"row {row_number}: {signal_id!r}")

        if is_active not in ALLOWED_ACTIVE:
            add(critical, "invalid_is_active", f"row {row_number}: {is_active!r}")
        elif is_active == "yes" and HEX16.fullmatch(opportunity_id):
            active_by_opportunity[opportunity_id].append(decision_id)

        if supersedes:
            if not HEX16.fullmatch(supersedes):
                add(critical, "invalid_supersedes_decision_id", f"row {row_number}: {supersedes!r}")
            elif supersedes == decision_id:
                add(critical, "decision_supersedes_itself", f"row {row_number}: {decision_id}")
            elif valid_decision_id:
                supersedes_edges[decision_id] = supersedes

        # Conditional business rules.
        if decision == "experiment_candidate":
            if not experiment_id:
                add(warnings, "experiment_candidate_without_experiment_id", f"row {row_number}")
            elif experiment_ids and experiment_id not in experiment_ids:
                bucket = critical if STRICT_EXPERIMENT_REFERENCE else warnings
                add(bucket, "experiment_id_not_found", f"row {row_number}: {experiment_id}")
        elif experiment_id:
            add(warnings, "experiment_id_on_non_experiment_decision", f"row {row_number}: {experiment_id}")

        if decision == "merged":
            if not HEX16.fullmatch(merged_into):
                add(critical, "merged_without_valid_target", f"row {row_number}: {merged_into!r}")
            elif merged_into == opportunity_id:
                add(critical, "opportunity_merged_into_itself", f"row {row_number}: {opportunity_id}")
        elif merged_into:
            add(warnings, "merge_target_on_non_merged_decision", f"row {row_number}: {merged_into}")

        if decision in {"monitor", "paused"} and not next_review_text:
            add(warnings, "monitor_or_paused_without_next_review", f"row {row_number}")
        if decision == "rejected" and next_review_text:
            add(warnings, "rejected_with_next_review", f"row {row_number}")

    # Cross-row validation after all decision IDs are known.
    for decision_id, supersedes in supersedes_edges.items():
        if supersedes not in decision_ids:
            add(critical, "superseded_decision_not_found", f"{decision_id} -> {supersedes}")
        elif clean(decision_by_id.get(supersedes, {}).get("opportunity_id")) != clean(decision_by_id.get(decision_id, {}).get("opportunity_id")):
            add(critical, "supersedes_different_opportunity", f"{decision_id} -> {supersedes}")

    # Detect supersession cycles.
    for start in supersedes_edges:
        seen = set()
        current = start
        while current in supersedes_edges:
            if current in seen:
                add(critical, "supersession_cycle", start)
                break
            seen.add(current)
            current = supersedes_edges[current]

    for opportunity_id, active_ids in active_by_opportunity.items():
        if len(active_ids) > 1:
            add(critical, "multiple_active_decisions", f"{opportunity_id}: {', '.join(active_ids)}")

    # If a decision supersedes another, the older decision must be inactive and the newer one active.
    for new_id, old_id in supersedes_edges.items():
        old_active = clean(decision_by_id.get(old_id, {}).get("is_active")).casefold()
        new_active = clean(decision_by_id.get(new_id, {}).get("is_active")).casefold()
        if old_id in decision_by_id and old_active != "no":
            add(critical, "superseded_decision_still_active", f"{old_id} superseded by {new_id}")
        if new_id in decision_by_id and new_active != "yes":
            add(warnings, "superseding_decision_not_active", f"{new_id} supersedes {old_id}")

    print("\nDecision summary:")
    print(f"  Decision rows: {len(rows)}")
    print(f"  Active opportunities with decisions: {len(active_by_opportunity)}")
    print(f"  Current portfolio opportunities available: {len(current_opportunity_ids)}")

    print("\nDecision counts:")
    for value, count in sorted(Counter(clean(row.get("decision")) for row in rows).items()):
        print(f"  {value or '(blank)'}: {count}")

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

    if warnings:
        print(f"\n::warning::Decision QA detected {sum(warnings.values())} warning(s).")
    if critical:
        print(f"\n::error::Decision QA failed with {sum(critical.values())} critical issue(s).")
        return 1

    print("\nDECISION QA PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
