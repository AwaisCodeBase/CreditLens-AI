"""Read-only data-quality profile for a transaction CSV.

The profiler reads one row at a time and keeps counters, not a copy of the
file. It never writes to the source file and never replaces unusual values.

Duplicate detection is optional and capped. When it is on, each distinct raw
row adds one 16-byte digest to a Python set until ``duplicate_fingerprint_limit``.
Python sets store hash-table slots and pointers, so the cost is much larger
than 16 bytes per row. Around one million distinct rows, expect on the order
of 100 MB. The default limit is 100,000 fingerprints, about one tenth of that.
When the limit is reached, fingerprinting stops, duplicate totals are withheld,
and the status is ``incomplete``. Pass ``detect_duplicates=False`` to skip the
sets entirely. Every other check still runs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from creditlens_contracts.schema import (
    EXPECTED_COLUMN_NAMES,
    SCHEMA_CONTRACT_VERSION,
    SchemaError,
    TransactionRecord,
    parse_row,
    validate_header,
)

# Review bands, not deletion rules. A value outside a band is counted and kept.
REVIEW_EXPONENTS: tuple[int, ...] = (6, 9, 12)
AMOUNT_COLUMNS: tuple[str, ...] = ("TRANSFERAMOUNT", "CHARGEAMOUNT", "PREVIOUSBALANCE")
PROFILE_COLUMNS: tuple[str, ...] = (
    "AGE",
    "GENDER",
    "EDUCATIONLEVELNAME",
    "MONTHLYINCOMERANGE",
    "JOBINFORMATION",
    "SUBCITY",
    "SECTOR",
    "CITYNAME",
    "STATENAME",
    "COUNTRYNAME",
)
SPARSE_COLUMNS: tuple[str, ...] = (
    "EDUCATIONLEVELNAME",
    "MONTHLYINCOMERANGE",
    "JOBINFORMATION",
    "SUBCITY",
    "SECTOR",
    "CITYNAME",
    "STATENAME",
    "COUNTRYNAME",
)
CATEGORY_LIST_LIMIT = 50
DAY_LIST_LIMIT = 400
DEFAULT_SUBSCRIBER_LIMIT = 100_000
DEFAULT_DUPLICATE_FINGERPRINT_LIMIT = 100_000
DEFAULT_SERVICE_DISTINCT_LIMIT = 1_000
DUPLICATE_DIGEST_BYTES = 16
SPARSE_BLANK_SHARE = Decimal("0.5")

_PROFILE_COLUMN_SET = set(PROFILE_COLUMNS)
if not _PROFILE_COLUMN_SET <= set(EXPECTED_COLUMN_NAMES):
    raise RuntimeError("Profile columns drifted away from the schema contract.")
if not set(AMOUNT_COLUMNS) <= set(EXPECTED_COLUMN_NAMES):
    raise RuntimeError("Amount columns drifted away from the schema contract.")


@dataclass(frozen=True, slots=True)
class AmountReview:
    """Aggregate review of one money column. No exact observed value is stored."""

    negative_count: int
    zero_count: int
    above_threshold_counts: tuple[tuple[int, int], ...]
    largest_absolute_magnitude: int | None


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Aggregate profile. Safe to print: it has counts, not source rows."""

    input_filename: str
    profiled_at: datetime
    row_count: int
    rows_parsed: int
    schema_contract_version: str
    schema_status: str
    body_scanned: bool
    schema_errors: tuple[tuple[str, int], ...]
    blank_row_counts: tuple[tuple[str, int], ...]
    age_buckets: tuple[tuple[str, int], ...]
    amount_reviews: tuple[tuple[str, AmountReview], ...]
    duplicate_detection_enabled: bool
    duplicate_detection_status: str
    duplicate_fingerprint_limit: int | None
    duplicate_extra_rows: int | None
    duplicate_distinct_rows: int | None
    service_allowlisted_counts: tuple[tuple[str, int], ...]
    service_non_allowlisted_rows: int
    service_distinct_count: int | None
    service_distinct_complete: bool
    currency_counts: tuple[tuple[str, int], ...]
    currency_rows_unlisted: int
    rows_by_day: tuple[tuple[str, int], ...]
    day_rows_unlisted: int
    subscriber_results_status: str
    subscribers_stored: int
    subscriber_rows_not_covered: int
    subscriber_activity: tuple[tuple[str, int], ...]
    subscriber_nonblank_coverage: tuple[tuple[str, int], ...]
    subscriber_inconsistent_profiles: tuple[tuple[str, int], ...]
    warnings: tuple[str, ...]


@dataclass(slots=True)
class _AmountAccumulator:
    negative_count: int = 0
    zero_count: int = 0
    above_counts: tuple[int, ...] = (0, 0, 0)
    largest_absolute_magnitude: int | None = None

    def observe(self, value: Decimal) -> None:
        if value < 0:
            self.negative_count += 1
        elif value == 0:
            self.zero_count += 1
        updated = list(self.above_counts)
        for index, exponent in enumerate(REVIEW_EXPONENTS):
            if value > Decimal(10) ** exponent:
                updated[index] += 1
        self.above_counts = (updated[0], updated[1], updated[2])
        if value != 0:
            magnitude = abs(value).adjusted()
            if (
                self.largest_absolute_magnitude is None
                or magnitude > self.largest_absolute_magnitude
            ):
                self.largest_absolute_magnitude = magnitude

    def finish(self) -> AmountReview:
        return AmountReview(
            negative_count=self.negative_count,
            zero_count=self.zero_count,
            above_threshold_counts=tuple(
                zip(REVIEW_EXPONENTS, self.above_counts, strict=True)
            ),
            largest_absolute_magnitude=self.largest_absolute_magnitude,
        )


@dataclass(slots=True)
class _SubscriberState:
    rows: int = 0
    first_digest: dict[str, bytes] | None = None
    mismatched: set[str] | None = None
    seen_nonblank: set[str] | None = None

    def __post_init__(self) -> None:
        if self.first_digest is None:
            self.first_digest = {}
        if self.mismatched is None:
            self.mismatched = set()
        if self.seen_nonblank is None:
            self.seen_nonblank = set()


def profile_transactions(
    path: Path,
    *,
    detect_duplicates: bool = True,
    duplicate_fingerprint_limit: int = DEFAULT_DUPLICATE_FINGERPRINT_LIMIT,
    service_allowlist: frozenset[str] | set[str] | None = None,
    clock: Callable[[], datetime] | None = None,
    subscriber_limit: int = DEFAULT_SUBSCRIBER_LIMIT,
) -> QualityReport:
    """Profile ``path`` without modifying it.

    ``detect_duplicates`` controls only the duplicate-hash sets. Set it to
    false to skip that memory cost. ``duplicate_fingerprint_limit`` stops
    those sets from growing without a bound. Subscriber ids stay in the
    running tally and are not copied into the returned report. Service names
    are counted in aggregate unless they are in ``service_allowlist``.
    """

    if duplicate_fingerprint_limit < 0:
        raise ValueError("duplicate_fingerprint_limit must be zero or greater.")
    allowlist = frozenset(service_allowlist or ())

    source = Path(path)
    profiled_at = (clock or _utc_now)()
    filename = source.name
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header_error = _header_error(reader.fieldnames)
        if header_error is not None:
            return _empty_report(
                filename=filename,
                profiled_at=profiled_at,
                detect_duplicates=detect_duplicates,
                schema_errors=((header_error, 1),),
            )
        return _profile_rows(
            reader=reader,
            filename=filename,
            profiled_at=profiled_at,
            detect_duplicates=detect_duplicates,
            duplicate_fingerprint_limit=duplicate_fingerprint_limit,
            service_allowlist=allowlist,
            subscriber_limit=subscriber_limit,
        )


def render_report(report: QualityReport) -> str:
    """Render an aggregate report. This text is what the CLI writes."""

    lines = [
        "CreditLens transaction quality report",
        f"input_filename: {report.input_filename}",
        f"profiled_at: {report.profiled_at.isoformat(timespec='seconds')}",
        f"row_count: {report.row_count}",
        f"rows_parsed: {report.rows_parsed}",
        f"schema_contract_version: {report.schema_contract_version}",
        f"schema_status: {report.schema_status}",
        f"body_scanned: {str(report.body_scanned).lower()}",
        f"duplicate_detection_enabled: {str(report.duplicate_detection_enabled).lower()}",
        f"duplicate_detection_status: {report.duplicate_detection_status}",
        "",
        "schema_errors:",
    ]
    lines.extend(_count_lines(report.schema_errors))
    lines.extend(["", "blank_row_counts:"])
    lines.extend(_count_lines(report.blank_row_counts))
    lines.extend(["", "age_buckets:"])
    lines.extend(_count_lines(report.age_buckets))
    lines.extend(["", "amount_reviews:"])
    for column_name, review in report.amount_reviews:
        lines.append(f"  {column_name}:")
        lines.append(f"    negative: {review.negative_count}")
        lines.append(f"    zero: {review.zero_count}")
        for exponent, count in review.above_threshold_counts:
            lines.append(f"    above_10^{exponent}: {count}")
        magnitude = _format_magnitude(review.largest_absolute_magnitude)
        lines.append(f"    largest_absolute_magnitude: {magnitude}")
    lines.extend(["", "duplicates:"])
    lines.extend(_duplicate_lines(report))
    lines.extend(["", "service_counts:"])
    if report.service_allowlisted_counts:
        lines.extend(_count_lines(report.service_allowlisted_counts, indent="  "))
    else:
        lines.append("  allowlisted: none")
    lines.append(f"  non_allowlisted_rows: {report.service_non_allowlisted_rows}")
    if report.service_distinct_complete:
        lines.append(f"  distinct_services: {report.service_distinct_count}")
    else:
        lines.append("  distinct_services: unavailable")
    lines.extend(["", "currency_counts:"])
    lines.extend(_count_lines(report.currency_counts, indent="  "))
    lines.append(f"  unlisted_rows: {report.currency_rows_unlisted}")
    lines.extend(["", "rows_by_day:"])
    lines.extend(_count_lines(report.rows_by_day, indent="  "))
    lines.append(f"  unlisted_rows: {report.day_rows_unlisted}")
    lines.extend(
        [
            "",
            f"subscriber_results_status: {report.subscriber_results_status}",
            f"subscribers_stored: {report.subscribers_stored}",
            f"subscriber_rows_not_covered: {report.subscriber_rows_not_covered}",
            f"subscriber_results_scope: {_subscriber_scope(report)}",
            _subscriber_status_note(report),
            "",
            "subscriber_activity:",
        ]
    )
    lines.extend(_count_lines(report.subscriber_activity))
    lines.extend(["", "subscriber_nonblank_coverage:"])
    lines.extend(_count_lines(report.subscriber_nonblank_coverage))
    lines.extend(["", "subscriber_inconsistent_profiles:"])
    lines.extend(_count_lines(report.subscriber_inconsistent_profiles))
    lines.extend(["", "warnings:"])
    if report.warnings:
        lines.extend(f"  - {code}" for code in report.warnings)
    else:
        lines.append("  - none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run the profiler from the command line.

    Reports are printed to the terminal, or written under ``outputs/`` when
    ``--output`` is set. The source CSV is opened for reading only.
    """

    parser = argparse.ArgumentParser(
        prog="python -m creditlens_contracts",
        description=(
            "Profile a transaction CSV in one read-only pass. "
            "Duplicate detection stores about 100 MB of hash-set overhead "
            "per million distinct rows and stops at the fingerprint limit. "
            "Use --no-duplicates to skip it."
        ),
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--no-duplicates",
        action="store_true",
        help="Skip duplicate detection and its hash sets. Other checks still run.",
    )
    parser.add_argument(
        "--duplicate-fingerprint-limit",
        type=int,
        default=DEFAULT_DUPLICATE_FINGERPRINT_LIMIT,
        help=(
            "Maximum distinct row fingerprints to store. "
            "When reached, duplicate totals are withheld."
        ),
    )
    args = parser.parse_args(argv)
    if args.output is not None and not _output_is_allowed(args.output):
        print("Output must be a file inside the outputs directory.", file=sys.stderr)
        return 2
    try:
        report = profile_transactions(
            args.input,
            detect_duplicates=not args.no_duplicates,
            duplicate_fingerprint_limit=args.duplicate_fingerprint_limit,
        )
    except FileNotFoundError:
        print("Input file was not found.", file=sys.stderr)
        return 2
    except UnicodeError:
        print("Input file is not valid UTF-8.", file=sys.stderr)
        return 2
    text = render_report(report)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    if report.schema_status == "failed":
        return 1
    return 0


def _profile_rows(
    *,
    reader: csv.DictReader[str],
    filename: str,
    profiled_at: datetime,
    detect_duplicates: bool,
    duplicate_fingerprint_limit: int,
    service_allowlist: frozenset[str],
    subscriber_limit: int,
) -> QualityReport:
    schema_errors: Counter[str] = Counter()
    blank_counts: Counter[str] = Counter()
    age_buckets: Counter[str] = Counter(
        {
            "blank": 0,
            "negative": 0,
            "zero": 0,
            "under_18": 0,
            "age_18_to_100": 0,
            "over_100": 0,
        }
    )
    amounts = {column: _AmountAccumulator() for column in AMOUNT_COLUMNS}
    seen_rows: set[bytes] | None = set() if detect_duplicates else None
    repeated_rows: set[bytes] | None = set() if detect_duplicates else None
    duplicate_extra_rows = 0
    duplicate_incomplete = False
    service_allowlisted: dict[str, int] = {}
    service_non_allowlisted_rows = 0
    service_digests: set[bytes] | None = set()
    currencies: dict[str, int] = {}
    currency_unlisted = 0
    days: dict[str, int] = {}
    day_unlisted = 0
    subscribers: dict[str, _SubscriberState] = {}
    subscriber_rows_not_covered = 0
    row_count = 0
    rows_parsed = 0

    for row in reader:
        row_count += 1
        if (
            seen_rows is not None
            and repeated_rows is not None
            and not duplicate_incomplete
        ):
            digest = _row_digest(row)
            if digest in seen_rows:
                duplicate_extra_rows += 1
                repeated_rows.add(digest)
            elif len(seen_rows) < duplicate_fingerprint_limit:
                seen_rows.add(digest)
            else:
                duplicate_incomplete = True
        try:
            record = parse_row(row)
        except SchemaError as exc:
            schema_errors[_schema_error_code(exc)] += 1
            continue
        rows_parsed += 1
        for column in EXPECTED_COLUMN_NAMES:
            if _is_blank(record, column):
                blank_counts[column] += 1
        age_buckets[_age_bucket(record.AGE)] += 1
        for column in AMOUNT_COLUMNS:
            amounts[column].observe(getattr(record, column))
        if record.SERVICENAME in service_allowlist:
            service_allowlisted[record.SERVICENAME] = (
                service_allowlisted.get(record.SERVICENAME, 0) + 1
            )
        else:
            service_non_allowlisted_rows += 1
        if service_digests is not None:
            service_digest = hashlib.blake2s(
                record.SERVICENAME.encode("utf-8"),
                digest_size=DUPLICATE_DIGEST_BYTES,
            ).digest()
            if service_digest not in service_digests:
                if len(service_digests) < DEFAULT_SERVICE_DISTINCT_LIMIT:
                    service_digests.add(service_digest)
                else:
                    service_digests = None
        currency_unlisted = _note_unlisted(
            currencies, record.SHORTNAME, currency_unlisted, CATEGORY_LIST_LIMIT
        )
        day_unlisted = _note_unlisted(
            days,
            record.TRANSACTIONDATE.date().isoformat(),
            day_unlisted,
            DAY_LIST_LIMIT,
        )
        if not _observe_subscriber(subscribers, record, subscriber_limit):
            subscriber_rows_not_covered += 1

    if not detect_duplicates:
        duplicate_status = "disabled"
        published_extra = None
        published_distinct = None
        published_limit = None
    elif duplicate_incomplete:
        duplicate_status = "incomplete"
        published_extra = None
        published_distinct = None
        published_limit = duplicate_fingerprint_limit
    else:
        duplicate_status = "complete"
        published_extra = duplicate_extra_rows
        published_distinct = len(repeated_rows) if repeated_rows is not None else 0
        published_limit = duplicate_fingerprint_limit

    return _finish_report(
        filename=filename,
        profiled_at=profiled_at,
        row_count=row_count,
        rows_parsed=rows_parsed,
        detect_duplicates=detect_duplicates,
        schema_errors=schema_errors,
        blank_counts=blank_counts,
        age_buckets=age_buckets,
        amounts=amounts,
        duplicate_detection_status=duplicate_status,
        duplicate_fingerprint_limit=published_limit,
        duplicate_extra_rows=published_extra,
        duplicate_distinct_rows=published_distinct,
        service_allowlisted=service_allowlisted,
        service_non_allowlisted_rows=service_non_allowlisted_rows,
        service_distinct_count=(
            len(service_digests) if service_digests is not None else None
        ),
        service_distinct_complete=service_digests is not None,
        currencies=currencies,
        currency_unlisted=currency_unlisted,
        days=days,
        day_unlisted=day_unlisted,
        subscribers=subscribers,
        subscriber_rows_not_covered=subscriber_rows_not_covered,
        body_scanned=True,
    )


def _finish_report(
    *,
    filename: str,
    profiled_at: datetime,
    row_count: int,
    rows_parsed: int,
    detect_duplicates: bool,
    schema_errors: Counter[str],
    blank_counts: Counter[str],
    age_buckets: Counter[str],
    amounts: dict[str, _AmountAccumulator],
    duplicate_detection_status: str,
    duplicate_fingerprint_limit: int | None,
    duplicate_extra_rows: int | None,
    duplicate_distinct_rows: int | None,
    service_allowlisted: dict[str, int],
    service_non_allowlisted_rows: int,
    service_distinct_count: int | None,
    service_distinct_complete: bool,
    currencies: dict[str, int],
    currency_unlisted: int,
    days: dict[str, int],
    day_unlisted: int,
    subscribers: dict[str, _SubscriberState],
    subscriber_rows_not_covered: int,
    body_scanned: bool,
) -> QualityReport:
    blank_rows = tuple(
        (column, blank_counts[column]) for column in EXPECTED_COLUMN_NAMES
    )
    coverage = tuple(
        (
            column,
            sum(
                1
                for state in subscribers.values()
                if state.seen_nonblank is not None and column in state.seen_nonblank
            ),
        )
        for column in PROFILE_COLUMNS
    )
    inconsistent = tuple(
        (
            column,
            sum(
                1
                for state in subscribers.values()
                if state.mismatched is not None and column in state.mismatched
            ),
        )
        for column in PROFILE_COLUMNS
    )
    activity = _activity_counts(subscribers)
    report = QualityReport(
        input_filename=filename,
        profiled_at=profiled_at,
        row_count=row_count,
        rows_parsed=rows_parsed,
        schema_contract_version=SCHEMA_CONTRACT_VERSION,
        schema_status="failed" if schema_errors else "ok",
        body_scanned=body_scanned,
        schema_errors=tuple(sorted(schema_errors.items())),
        blank_row_counts=blank_rows,
        age_buckets=tuple(age_buckets.items()),
        amount_reviews=tuple(
            (column, amounts[column].finish()) for column in AMOUNT_COLUMNS
        ),
        duplicate_detection_enabled=detect_duplicates,
        duplicate_detection_status=duplicate_detection_status,
        duplicate_fingerprint_limit=duplicate_fingerprint_limit,
        duplicate_extra_rows=duplicate_extra_rows,
        duplicate_distinct_rows=duplicate_distinct_rows,
        service_allowlisted_counts=tuple(service_allowlisted.items()),
        service_non_allowlisted_rows=service_non_allowlisted_rows,
        service_distinct_count=service_distinct_count,
        service_distinct_complete=service_distinct_complete,
        currency_counts=tuple(currencies.items()),
        currency_rows_unlisted=currency_unlisted,
        rows_by_day=tuple(days.items()),
        day_rows_unlisted=day_unlisted,
        subscriber_results_status=(
            "partial" if subscriber_rows_not_covered else "complete"
        ),
        subscribers_stored=len(subscribers),
        subscriber_rows_not_covered=subscriber_rows_not_covered,
        subscriber_activity=activity,
        subscriber_nonblank_coverage=coverage,
        subscriber_inconsistent_profiles=inconsistent,
        warnings=(),
    )
    return QualityReport(
        input_filename=report.input_filename,
        profiled_at=report.profiled_at,
        row_count=report.row_count,
        rows_parsed=report.rows_parsed,
        schema_contract_version=report.schema_contract_version,
        schema_status=report.schema_status,
        body_scanned=report.body_scanned,
        schema_errors=report.schema_errors,
        blank_row_counts=report.blank_row_counts,
        age_buckets=report.age_buckets,
        amount_reviews=report.amount_reviews,
        duplicate_detection_enabled=report.duplicate_detection_enabled,
        duplicate_detection_status=report.duplicate_detection_status,
        duplicate_fingerprint_limit=report.duplicate_fingerprint_limit,
        duplicate_extra_rows=report.duplicate_extra_rows,
        duplicate_distinct_rows=report.duplicate_distinct_rows,
        service_allowlisted_counts=report.service_allowlisted_counts,
        service_non_allowlisted_rows=report.service_non_allowlisted_rows,
        service_distinct_count=report.service_distinct_count,
        service_distinct_complete=report.service_distinct_complete,
        currency_counts=report.currency_counts,
        currency_rows_unlisted=report.currency_rows_unlisted,
        rows_by_day=report.rows_by_day,
        day_rows_unlisted=report.day_rows_unlisted,
        subscriber_results_status=report.subscriber_results_status,
        subscribers_stored=report.subscribers_stored,
        subscriber_rows_not_covered=report.subscriber_rows_not_covered,
        subscriber_activity=report.subscriber_activity,
        subscriber_nonblank_coverage=report.subscriber_nonblank_coverage,
        subscriber_inconsistent_profiles=report.subscriber_inconsistent_profiles,
        warnings=_warning_codes(report),
    )


def _observe_subscriber(
    subscribers: dict[str, _SubscriberState],
    record: TransactionRecord,
    subscriber_limit: int,
) -> bool:
    """Return true when this row is covered by the stored subscriber table."""

    state = subscribers.get(record.SUBSCRIBERID)
    if state is None:
        if len(subscribers) >= subscriber_limit:
            return False
        state = _SubscriberState()
        subscribers[record.SUBSCRIBERID] = state
    state.rows += 1
    assert state.first_digest is not None
    assert state.mismatched is not None
    assert state.seen_nonblank is not None
    for column in PROFILE_COLUMNS:
        token = _profile_token(record, column)
        if token is None:
            continue
        state.seen_nonblank.add(column)
        digest = hashlib.blake2s(token.encode("utf-8"), digest_size=16).digest()
        previous = state.first_digest.get(column)
        if previous is None:
            state.first_digest[column] = digest
        elif previous != digest:
            state.mismatched.add(column)
    return True


def _profile_token(record: TransactionRecord, column: str) -> str | None:
    if column == "AGE":
        if record.AGE is None:
            return None
        return format(record.AGE, "f")
    text = getattr(record, column)
    if text == "":
        return None
    return text


def _note_unlisted(counts: dict[str, int], name: str, unlisted: int, limit: int) -> int:
    """Count ``name`` until ``limit`` distinct names are stored.

    Further names are not written into the report. Only their row count is kept.
    """

    if name in counts:
        counts[name] += 1
        return unlisted
    if len(counts) < limit:
        counts[name] = 1
        return unlisted
    return unlisted + 1


def _is_blank(record: TransactionRecord, column: str) -> bool:
    value = getattr(record, column)
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    return False


def _age_bucket(age: Decimal | None) -> str:
    if age is None:
        return "blank"
    if age < 0:
        return "negative"
    if age == 0:
        return "zero"
    if age < 18:
        return "under_18"
    if age <= 100:
        return "age_18_to_100"
    return "over_100"


def _activity_counts(
    subscribers: Mapping[str, _SubscriberState],
) -> tuple[tuple[str, int], ...]:
    buckets = {"1": 0, "2_to_10": 0, "11_to_100": 0, "over_100": 0}
    for state in subscribers.values():
        if state.rows == 1:
            buckets["1"] += 1
        elif state.rows <= 10:
            buckets["2_to_10"] += 1
        elif state.rows <= 100:
            buckets["11_to_100"] += 1
        else:
            buckets["over_100"] += 1
    return tuple(buckets.items())


def _warning_codes(report: QualityReport) -> tuple[str, ...]:
    if report.rows_parsed == 0:
        return ()
    codes: list[str] = []
    ages = dict(report.age_buckets)
    if ages.get("blank", 0) > 0:
        codes.append("age_missing")
    if any(ages.get(name, 0) > 0 for name in ("negative", "zero", "under_18", "over_100")):
        codes.append("age_outside_review_band")
    reviews = dict(report.amount_reviews)
    if _has_extreme(reviews["TRANSFERAMOUNT"]):
        codes.append("extreme_transfer_amount")
    if _has_extreme(reviews["CHARGEAMOUNT"]):
        codes.append("extreme_charge_amount")
    if _has_extreme(reviews["PREVIOUSBALANCE"]):
        codes.append("extreme_previous_balance")
    if reviews["TRANSFERAMOUNT"].negative_count > 0:
        codes.append("negative_transfer_amount")
    if reviews["CHARGEAMOUNT"].negative_count > 0:
        codes.append("negative_charge_amount")
    if reviews["PREVIOUSBALANCE"].negative_count > 0:
        codes.append("negative_previous_balance")
    if report.duplicate_detection_status == "incomplete":
        codes.append("duplicate_detection_incomplete")
    elif report.duplicate_detection_status == "complete" and (
        report.duplicate_extra_rows or 0
    ) > 0:
        codes.append("duplicate_rows")
    if any(count > 0 for _, count in report.subscriber_inconsistent_profiles):
        codes.append("inconsistent_subscriber_profile")
    blanks = dict(report.blank_row_counts)
    sparse = any(
        Decimal(blanks.get(column, 0)) / Decimal(report.rows_parsed) >= SPARSE_BLANK_SHARE
        for column in SPARSE_COLUMNS
    )
    if sparse:
        codes.append("sparse_demographic_fields")
    if report.subscriber_results_status == "partial":
        codes.append("subscriber_check_limit_reached")
    return tuple(codes)


def _has_extreme(review: AmountReview) -> bool:
    return any(count > 0 for _, count in review.above_threshold_counts)


def _row_digest(row: Mapping[str, str | None]) -> bytes:
    values = tuple(row.get(column) or "" for column in EXPECTED_COLUMN_NAMES)
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2s(payload, digest_size=DUPLICATE_DIGEST_BYTES).digest()


def _schema_error_code(exc: SchemaError) -> str:
    return exc.code


def _header_error(fieldnames: list[str] | None) -> str | None:
    if fieldnames is None or any(name is None for name in fieldnames):
        return "header:missing"
    try:
        validate_header(list(fieldnames))
    except SchemaError as exc:
        return exc.code
    return None


def _empty_report(
    *,
    filename: str,
    profiled_at: datetime,
    detect_duplicates: bool,
    schema_errors: tuple[tuple[str, int], ...],
) -> QualityReport:
    zero_amounts = tuple(
        (
            column,
            AmountReview(
                negative_count=0,
                zero_count=0,
                above_threshold_counts=tuple((exponent, 0) for exponent in REVIEW_EXPONENTS),
                largest_absolute_magnitude=None,
            ),
        )
        for column in AMOUNT_COLUMNS
    )
    return QualityReport(
        input_filename=filename,
        profiled_at=profiled_at,
        row_count=0,
        rows_parsed=0,
        schema_contract_version=SCHEMA_CONTRACT_VERSION,
        schema_status="failed",
        body_scanned=False,
        schema_errors=schema_errors,
        blank_row_counts=tuple((column, 0) for column in EXPECTED_COLUMN_NAMES),
        age_buckets=(
            ("blank", 0),
            ("negative", 0),
            ("zero", 0),
            ("under_18", 0),
            ("age_18_to_100", 0),
            ("over_100", 0),
        ),
        amount_reviews=zero_amounts,
        duplicate_detection_enabled=detect_duplicates,
        duplicate_detection_status="unavailable",
        duplicate_fingerprint_limit=None,
        duplicate_extra_rows=None,
        duplicate_distinct_rows=None,
        service_allowlisted_counts=(),
        service_non_allowlisted_rows=0,
        service_distinct_count=None,
        service_distinct_complete=False,
        currency_counts=(),
        currency_rows_unlisted=0,
        rows_by_day=(),
        day_rows_unlisted=0,
        subscriber_results_status="unavailable",
        subscribers_stored=0,
        subscriber_rows_not_covered=0,
        subscriber_activity=(("1", 0), ("2_to_10", 0), ("11_to_100", 0), ("over_100", 0)),
        subscriber_nonblank_coverage=tuple((column, 0) for column in PROFILE_COLUMNS),
        subscriber_inconsistent_profiles=tuple((column, 0) for column in PROFILE_COLUMNS),
        warnings=(),
    )


def _duplicate_lines(report: QualityReport) -> list[str]:
    status = report.duplicate_detection_status
    if status == "complete":
        return [
            f"  extra_rows: {report.duplicate_extra_rows}",
            f"  distinct_rows_with_copies: {report.duplicate_distinct_rows}",
        ]
    if status == "incomplete":
        return [
            "  extra_rows: unavailable",
            "  distinct_rows_with_copies: unavailable",
            "  The duplicate fingerprint limit was reached. "
            "Partial duplicate counts are not reported.",
        ]
    if status == "unavailable":
        return [
            "  extra_rows: unavailable",
            "  distinct_rows_with_copies: unavailable",
            "  The body was not scanned. Duplicate results are unavailable.",
        ]
    return [
        "  extra_rows: not_run",
        "  distinct_rows_with_copies: not_run",
    ]


def _subscriber_scope(report: QualityReport) -> str:
    if report.subscriber_results_status == "partial":
        return "stored_subscribers_only"
    if report.subscriber_results_status == "unavailable":
        return "not_scanned"
    return "all_parsed_subscribers"


def _subscriber_status_note(report: QualityReport) -> str:
    if report.subscriber_results_status == "partial":
        return (
            "Subscriber counts and consistency results cover stored subscribers only."
        )
    if report.subscriber_results_status == "unavailable":
        return "The body was not scanned. Subscriber results are unavailable."
    return "Subscriber counts and consistency results cover every parsed subscriber."


def _count_lines(items: tuple[tuple[str, int], ...], *, indent: str = "  ") -> list[str]:
    if not items:
        return [f"{indent}none"]
    return [f"{indent}{name}: {count}" for name, count in items]


def _format_magnitude(exponent: int | None) -> str:
    if exponent is None:
        return "none"
    return f"10^{exponent}"


def _output_is_allowed(path: Path) -> bool:
    outputs = (Path.cwd() / "outputs").resolve()
    resolved = path.resolve()
    if resolved == outputs:
        return False
    try:
        resolved.relative_to(outputs)
    except ValueError:
        return False
    return True


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
