"""Aggregate quality profile for the labeled credit-card contract.

This pass counts parsed rows and schema failures. It does not change the
file, delete rows, or rewrite unusual codes. Parsing rules stay in
``default_clients.py``. The transaction profiler is a separate command.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import xlrd
from xlrd.compdoc import CompDocError

from creditlens_contracts.default_clients import (
    COLUMNS,
    SCHEMA_CONTRACT_VERSION,
    ColumnRole,
    DefaultClientRecord,
    LabelSchemaError,
    _csv_data_row,
    _parse_xls_row,
    _resolve_csv_header,
    _xls_data_start,
    parse_row,
    validate_header,
)

REVIEW_EXPONENTS: tuple[int, ...] = (6, 9, 12)
DEFAULT_DUPLICATE_FINGERPRINT_LIMIT = 100_000
DUPLICATE_DIGEST_BYTES = 16

_FILE_LEVEL_CODES = frozenset(
    {
        "header:missing",
        "header:mismatch",
        "sheet:mismatch",
        "workbook:unreadable",
        "record:fields_mismatch",
    }
)
_PAY_COLUMNS: tuple[str, ...] = (
    "PAY_0",
    "PAY_2",
    "PAY_3",
    "PAY_4",
    "PAY_5",
    "PAY_6",
)
_REPAYMENT_CODES: tuple[int, ...] = (-2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9)
_RECOGNIZED_REPAYMENT = frozenset(_REPAYMENT_CODES)
_AMOUNT_COLUMNS: tuple[str, ...] = tuple(
    column.field for column in COLUMNS if column.role is ColumnRole.DECIMAL
)
_WARNING_ORDER: tuple[str, ...] = (
    "target_class_absent",
    "undocumented_education_code",
    "undocumented_marriage_code",
    "undocumented_sex_code",
    "ambiguous_repayment_status",
    "undocumented_repayment_status",
    "age_outside_review_band",
    "nonpositive_credit_limit",
    "negative_payment_amount",
    "duplicate_rows",
    "duplicate_client_id",
    "duplicate_detection_incomplete",
)


@dataclass(frozen=True, slots=True)
class AmountReview:
    negative_count: int
    zero_count: int
    above_threshold_counts: tuple[tuple[int, int], ...]
    largest_absolute_magnitude: int | None


@dataclass(frozen=True, slots=True)
class DefaultClientQualityReport:
    """Aggregate profile. Counts are omitted when that section was not measured."""

    input_filename: str
    profiled_at: datetime
    row_count: int
    rows_parsed: int
    schema_contract_version: str
    schema_status: str
    body_scanned: bool
    scan_status: str
    quality_results_status: str
    schema_errors: tuple[tuple[str, int], ...]
    target_counts: tuple[tuple[str, int], ...] | None
    education_counts: tuple[tuple[str, int], ...] | None
    marriage_counts: tuple[tuple[str, int], ...] | None
    sex_counts: tuple[tuple[str, int], ...] | None
    repayment_counts: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] | None
    age_bands: tuple[tuple[str, int], ...] | None
    amount_reviews: tuple[tuple[str, AmountReview], ...] | None
    duplicate_detection_enabled: bool
    duplicate_detection_status: str
    duplicate_fingerprint_limit: int | None
    duplicate_extra_rows: int | None
    duplicate_distinct_rows: int | None
    repeated_id_extra_rows: int | None
    repeated_id_distinct_rows: int | None
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


def profile_default_clients(
    path: Path,
    *,
    detect_duplicates: bool = True,
    duplicate_fingerprint_limit: int = DEFAULT_DUPLICATE_FINGERPRINT_LIMIT,
    clock: object | None = None,
) -> DefaultClientQualityReport:
    """Profile a labeled client file without modifying it.

    CSV rows are read one at a time. An ``.xls`` workbook is held by
    ``xlrd``; this function does not keep a second list of records.
    Duplicate detection stores 16-byte fingerprints and client ids only
    until ``duplicate_fingerprint_limit``. Those ids are not copied into
    the returned report. Set ``detect_duplicates`` false to skip that memory.
    """

    if duplicate_fingerprint_limit < 0:
        raise ValueError("duplicate_fingerprint_limit must be zero or greater.")
    source = Path(path)
    profiled_at = _timestamp(clock)
    filename = source.name
    tally = _Tally(detect_duplicates=detect_duplicates, limit=duplicate_fingerprint_limit)
    try:
        for item in _scan(source):
            tally.row_count += 1
            if isinstance(item, str):
                tally.schema_errors[item] += 1
                continue
            tally.observe(item)
    except LabelSchemaError as exc:
        return _interrupted_report(
            filename=filename,
            profiled_at=profiled_at,
            detect_duplicates=detect_duplicates,
            schema_errors=((exc.code, 1),),
        )
    return tally.finish(filename=filename, profiled_at=profiled_at)


def render_default_client_report(report: DefaultClientQualityReport) -> str:
    """Render aggregate counts. Unavailable sections are not printed as zero."""

    lines = [
        "CreditLens labeled-client quality report",
        f"input_filename: {report.input_filename}",
        f"profiled_at: {report.profiled_at.isoformat(timespec='seconds')}",
        f"row_count: {report.row_count}",
        f"rows_parsed: {report.rows_parsed}",
        f"schema_contract_version: {report.schema_contract_version}",
        f"schema_status: {report.schema_status}",
        f"body_scanned: {str(report.body_scanned).lower()}",
        f"scan_status: {report.scan_status}",
        f"quality_results_status: {report.quality_results_status}",
        f"duplicate_detection_enabled: {str(report.duplicate_detection_enabled).lower()}",
        f"duplicate_detection_status: {report.duplicate_detection_status}",
        "",
        "schema_errors:",
    ]
    lines.extend(_count_lines(report.schema_errors))
    lines.extend(["", "target_counts:"])
    lines.extend(_optional_counts(report.target_counts))
    lines.extend(["", "education_counts:"])
    lines.extend(_optional_counts(report.education_counts))
    lines.extend(["", "marriage_counts:"])
    lines.extend(_optional_counts(report.marriage_counts))
    lines.extend(["", "sex_counts:"])
    lines.extend(_optional_counts(report.sex_counts))
    lines.extend(["", "repayment_counts:"])
    lines.extend(_repayment_lines(report.repayment_counts))
    lines.extend(["", "age_bands:"])
    lines.extend(_optional_counts(report.age_bands))
    lines.extend(["", "amount_reviews:"])
    lines.extend(_amount_lines(report.amount_reviews))
    lines.extend(["", "duplicates:"])
    lines.extend(_duplicate_lines(report))
    lines.extend(["", "warnings:"])
    if report.warnings:
        lines.extend(f"  - {code}" for code in report.warnings)
    else:
        lines.append("  - none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Profile a labeled client file from the command line.

    The report is printed, or written under ``outputs/`` when ``--output``
    is set. The source file is opened for reading only.
    """

    parser = argparse.ArgumentParser(
        prog="python -m creditlens_contracts.default_clients_quality",
        description=(
            "Profile a labeled credit-card file in one read-only pass. "
            "Duplicate detection stores 16-byte fingerprints up to the "
            "configured limit. Use --no-duplicates to skip it."
        ),
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--no-duplicates",
        action="store_true",
        help="Skip duplicate detection and its fingerprint sets. Other checks still run.",
    )
    parser.add_argument(
        "--duplicate-fingerprint-limit",
        type=int,
        default=DEFAULT_DUPLICATE_FINGERPRINT_LIMIT,
        help=(
            "Maximum distinct full-row fingerprints and distinct ids to store. "
            "When reached, duplicate totals are withheld."
        ),
    )
    args = parser.parse_args(argv)
    if args.output is not None and not _output_is_allowed(args.output):
        print("Output must be a file inside the outputs directory.", file=sys.stderr)
        return 2
    try:
        report = profile_default_clients(
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
    text = render_default_client_report(report)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    if report.schema_status == "failed":
        return 1
    return 0


class _Tally:
    def __init__(self, *, detect_duplicates: bool, limit: int) -> None:
        self.detect_duplicates = detect_duplicates
        self.limit = limit
        self.row_count = 0
        self.rows_parsed = 0
        self.schema_errors: Counter[str] = Counter()
        self.target = Counter({"0": 0, "1": 0})
        self.education = Counter({str(code): 0 for code in range(7)})
        self.education["other"] = 0
        self.marriage = Counter({str(code): 0 for code in range(4)})
        self.marriage["other"] = 0
        self.sex = Counter({"1": 0, "2": 0, "undocumented": 0})
        self.repayment = {
            column: Counter({str(code): 0 for code in _REPAYMENT_CODES})
            for column in _PAY_COLUMNS
        }
        for counts in self.repayment.values():
            counts["other"] = 0
        self.age = Counter(
            {
                "negative": 0,
                "zero": 0,
                "under_18": 0,
                "age_18_to_100": 0,
                "over_100": 0,
            }
        )
        self.amounts = {column: _AmountAccumulator() for column in _AMOUNT_COLUMNS}
        self.ambiguous_repayment = False
        self.undocumented_repayment = False
        self.age_outside = False
        if detect_duplicates:
            self.seen_rows: set[bytes] | None = _new_set()
            self.repeated_rows: set[bytes] | None = _new_set()
            self.seen_ids: set[int] | None = _new_set()
            self.repeated_ids: set[int] | None = _new_set()
        else:
            self.seen_rows = None
            self.repeated_rows = None
            self.seen_ids = None
            self.repeated_ids = None
        self.duplicate_extra_rows = 0
        self.repeated_id_extra_rows = 0
        self.duplicate_incomplete = False

    def observe(self, record: DefaultClientRecord) -> None:
        self.rows_parsed += 1
        self.target[str(record.default_payment_next_month)] += 1
        self._count_code(self.education, record.EDUCATION, documented_max=6)
        self._count_code(self.marriage, record.MARRIAGE, documented_max=3)
        if record.SEX in (1, 2):
            self.sex[str(record.SEX)] += 1
        else:
            self.sex["undocumented"] += 1
        for column in _PAY_COLUMNS:
            code = getattr(record, column)
            counts = self.repayment[column]
            if code in _RECOGNIZED_REPAYMENT:
                counts[str(code)] += 1
            else:
                counts["other"] += 1
            if code in (-2, 0):
                self.ambiguous_repayment = True
            elif code not in _RECOGNIZED_REPAYMENT:
                self.undocumented_repayment = True
        self._observe_age(record.AGE)
        for column in _AMOUNT_COLUMNS:
            self.amounts[column].observe(getattr(record, column))
        self._observe_duplicates(record)

    def finish(self, *, filename: str, profiled_at: datetime) -> DefaultClientQualityReport:
        duplicate_status, extra, distinct, id_extra, id_distinct, published_limit = (
            _duplicate_publication(self)
        )
        report = DefaultClientQualityReport(
            input_filename=filename,
            profiled_at=profiled_at,
            row_count=self.row_count,
            rows_parsed=self.rows_parsed,
            schema_contract_version=SCHEMA_CONTRACT_VERSION,
            schema_status="failed" if self.schema_errors else "ok",
            body_scanned=True,
            scan_status="complete",
            quality_results_status="complete",
            schema_errors=tuple(sorted(self.schema_errors.items())),
            target_counts=_ordered(self.target, ("0", "1")),
            education_counts=_ordered(self.education, tuple(str(code) for code in range(7)) + ("other",)),
            marriage_counts=_ordered(self.marriage, tuple(str(code) for code in range(4)) + ("other",)),
            sex_counts=_ordered(self.sex, ("1", "2", "undocumented")),
            repayment_counts=tuple(
                (column, _ordered(self.repayment[column], tuple(str(code) for code in _REPAYMENT_CODES) + ("other",)))
                for column in _PAY_COLUMNS
            ),
            age_bands=_ordered(
                self.age,
                ("negative", "zero", "under_18", "age_18_to_100", "over_100"),
            ),
            amount_reviews=tuple(
                (column, self.amounts[column].finish()) for column in _AMOUNT_COLUMNS
            ),
            duplicate_detection_enabled=self.detect_duplicates,
            duplicate_detection_status=duplicate_status,
            duplicate_fingerprint_limit=published_limit,
            duplicate_extra_rows=extra,
            duplicate_distinct_rows=distinct,
            repeated_id_extra_rows=id_extra,
            repeated_id_distinct_rows=id_distinct,
            warnings=(),
        )
        return DefaultClientQualityReport(
            input_filename=report.input_filename,
            profiled_at=report.profiled_at,
            row_count=report.row_count,
            rows_parsed=report.rows_parsed,
            schema_contract_version=report.schema_contract_version,
            schema_status=report.schema_status,
            body_scanned=report.body_scanned,
            scan_status=report.scan_status,
            quality_results_status=report.quality_results_status,
            schema_errors=report.schema_errors,
            target_counts=report.target_counts,
            education_counts=report.education_counts,
            marriage_counts=report.marriage_counts,
            sex_counts=report.sex_counts,
            repayment_counts=report.repayment_counts,
            age_bands=report.age_bands,
            amount_reviews=report.amount_reviews,
            duplicate_detection_enabled=report.duplicate_detection_enabled,
            duplicate_detection_status=report.duplicate_detection_status,
            duplicate_fingerprint_limit=report.duplicate_fingerprint_limit,
            duplicate_extra_rows=report.duplicate_extra_rows,
            duplicate_distinct_rows=report.duplicate_distinct_rows,
            repeated_id_extra_rows=report.repeated_id_extra_rows,
            repeated_id_distinct_rows=report.repeated_id_distinct_rows,
            warnings=_warning_codes(report, self),
        )

    def _count_code(self, counts: Counter[str], code: int, *, documented_max: int) -> None:
        if 0 <= code <= documented_max:
            counts[str(code)] += 1
        else:
            counts["other"] += 1

    def _observe_age(self, age: int) -> None:
        if age < 0:
            self.age["negative"] += 1
            self.age_outside = True
        elif age == 0:
            self.age["zero"] += 1
            self.age_outside = True
        elif age < 18:
            self.age["under_18"] += 1
            self.age_outside = True
        elif age <= 100:
            self.age["age_18_to_100"] += 1
        else:
            self.age["over_100"] += 1
            self.age_outside = True

    def _observe_duplicates(self, record: DefaultClientRecord) -> None:
        if (
            not self.detect_duplicates
            or self.duplicate_incomplete
            or self.seen_rows is None
            or self.repeated_rows is None
            or self.seen_ids is None
            or self.repeated_ids is None
        ):
            return
        digest = _record_digest(record)
        if digest in self.seen_rows:
            self.duplicate_extra_rows += 1
            self.repeated_rows.add(digest)
        elif len(self.seen_rows) < self.limit:
            self.seen_rows.add(digest)
        else:
            self.duplicate_incomplete = True
            return
        if record.ID in self.seen_ids:
            self.repeated_id_extra_rows += 1
            self.repeated_ids.add(record.ID)
        elif len(self.seen_ids) < self.limit:
            self.seen_ids.add(record.ID)
        else:
            self.duplicate_incomplete = True


def _duplicate_publication(
    tally: _Tally,
) -> tuple[str, int | None, int | None, int | None, int | None, int | None]:
    if not tally.detect_duplicates:
        return "disabled", None, None, None, None, None
    if tally.duplicate_incomplete or tally.seen_rows is None or tally.repeated_rows is None:
        return "incomplete", None, None, None, None, tally.limit
    if tally.repeated_ids is None:
        return "incomplete", None, None, None, None, tally.limit
    return (
        "complete",
        tally.duplicate_extra_rows,
        len(tally.repeated_rows),
        tally.repeated_id_extra_rows,
        len(tally.repeated_ids),
        tally.limit,
    )


def _warning_codes(report: DefaultClientQualityReport, tally: _Tally) -> tuple[str, ...]:
    if report.rows_parsed == 0:
        codes: list[str] = []
    else:
        education_outside = _count(report.education_counts, "0") + _count(
            report.education_counts, "5"
        ) + _count(report.education_counts, "6") + _count(report.education_counts, "other")
        marriage_outside = _count(report.marriage_counts, "0") + _count(
            report.marriage_counts, "other"
        )
        sex_outside = _count(report.sex_counts, "undocumented")
        limit = tally.amounts["LIMIT_BAL"]
        negative_payment = any(
            tally.amounts[column].negative_count > 0
            for column in _AMOUNT_COLUMNS
            if column.startswith("PAY_AMT")
        )
        target_absent = _count(report.target_counts, "0") == 0 or _count(
            report.target_counts, "1"
        ) == 0
        flags = {
            "target_class_absent": target_absent,
            "undocumented_education_code": education_outside > 0,
            "undocumented_marriage_code": marriage_outside > 0,
            "undocumented_sex_code": sex_outside > 0,
            "ambiguous_repayment_status": tally.ambiguous_repayment,
            "undocumented_repayment_status": tally.undocumented_repayment,
            "age_outside_review_band": tally.age_outside,
            "nonpositive_credit_limit": limit.negative_count > 0 or limit.zero_count > 0,
            "negative_payment_amount": negative_payment,
        }
        codes = [code for code in _WARNING_ORDER if flags.get(code, False)]
    if report.duplicate_detection_status == "incomplete":
        codes.append("duplicate_detection_incomplete")
    elif report.duplicate_detection_status == "complete":
        if report.duplicate_extra_rows:
            codes.append("duplicate_rows")
        if report.repeated_id_extra_rows:
            codes.append("duplicate_client_id")
    return tuple(codes)


def _interrupted_report(
    *,
    filename: str,
    profiled_at: datetime,
    detect_duplicates: bool,
    schema_errors: tuple[tuple[str, int], ...],
) -> DefaultClientQualityReport:
    return DefaultClientQualityReport(
        input_filename=filename,
        profiled_at=profiled_at,
        row_count=0,
        rows_parsed=0,
        schema_contract_version=SCHEMA_CONTRACT_VERSION,
        schema_status="failed",
        body_scanned=False,
        scan_status="interrupted",
        quality_results_status="unavailable",
        schema_errors=schema_errors,
        target_counts=None,
        education_counts=None,
        marriage_counts=None,
        sex_counts=None,
        repayment_counts=None,
        age_bands=None,
        amount_reviews=None,
        duplicate_detection_enabled=detect_duplicates,
        duplicate_detection_status="unavailable",
        duplicate_fingerprint_limit=None,
        duplicate_extra_rows=None,
        duplicate_distinct_rows=None,
        repeated_id_extra_rows=None,
        repeated_id_distinct_rows=None,
        warnings=(),
    )


def _scan(path: Path) -> Iterator[DefaultClientRecord | str]:
    if path.suffix.lower() == ".xls":
        yield from _scan_xls(path)
    else:
        yield from _scan_csv(path)


def _scan_csv(path: Path) -> Iterator[DefaultClientRecord | str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            first = next(reader)
        except StopIteration:
            raise LabelSchemaError("CSV has no header row.", code="header:missing") from None
        header = _resolve_csv_header(first, reader)
        validate_header(header)
        for raw in reader:
            try:
                yield parse_row(_csv_data_row(header, raw))
            except LabelSchemaError as exc:
                if exc.code in _FILE_LEVEL_CODES:
                    raise
                yield exc.code


def _scan_xls(path: Path) -> Iterator[DefaultClientRecord | str]:
    try:
        book = xlrd.open_workbook(path, on_demand=True, formatting_info=False)
    except (xlrd.XLRDError, CompDocError) as exc:
        raise LabelSchemaError(
            "Workbook could not be read.",
            code="workbook:unreadable",
        ) from exc
    try:
        if tuple(book.sheet_names()) != ("Data",):
            raise LabelSchemaError(
                "Workbook does not contain exactly the Data sheet.",
                code="sheet:mismatch",
            )
        sheet = book.sheet_by_name("Data")
        if sheet.nrows == 0:
            raise LabelSchemaError("Workbook has no header row.", code="header:missing")
        data_start = _xls_data_start(sheet)
        for row_index in range(data_start, sheet.nrows):
            try:
                yield _parse_xls_row(sheet, row_index)
            except LabelSchemaError as exc:
                if exc.code in _FILE_LEVEL_CODES:
                    raise
                yield exc.code
    finally:
        book.release_resources()


def _new_set() -> set[object]:
    return set()


def _record_digest(record: DefaultClientRecord) -> bytes:
    values: list[str] = []
    for column in COLUMNS:
        value = getattr(record, column.field)
        if isinstance(value, Decimal):
            values.append(_canonical_decimal(value))
        else:
            values.append(str(value))
    payload = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.blake2s(payload, digest_size=DUPLICATE_DIGEST_BYTES).digest()


def _canonical_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _ordered(counts: Counter[str], labels: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    return tuple((label, counts[label]) for label in labels)


def _count(items: tuple[tuple[str, int], ...] | None, label: str) -> int:
    if items is None:
        return 0
    return next((count for name, count in items if name == label), 0)


def _optional_counts(items: tuple[tuple[str, int], ...] | None) -> list[str]:
    if items is None:
        return ["  unavailable"]
    return _count_lines(items)


def _count_lines(items: tuple[tuple[str, int], ...], *, indent: str = "  ") -> list[str]:
    if not items:
        return [f"{indent}none"]
    return [f"{indent}{name}: {count}" for name, count in items]


def _repayment_lines(
    columns: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] | None,
) -> list[str]:
    if columns is None:
        return ["  unavailable"]
    lines: list[str] = []
    for column, counts in columns:
        lines.append(f"  {column}:")
        lines.extend(_count_lines(counts, indent="    "))
    return lines


def _amount_lines(reviews: tuple[tuple[str, AmountReview], ...] | None) -> list[str]:
    if reviews is None:
        return ["  unavailable"]
    lines: list[str] = []
    for column, review in reviews:
        lines.append(f"  {column}:")
        lines.append(f"    negative: {review.negative_count}")
        lines.append(f"    zero: {review.zero_count}")
        for exponent, count in review.above_threshold_counts:
            lines.append(f"    above_10^{exponent}: {count}")
        lines.append(
            f"    largest_absolute_magnitude: {_format_magnitude(review.largest_absolute_magnitude)}"
        )
    return lines


def _duplicate_lines(report: DefaultClientQualityReport) -> list[str]:
    status = report.duplicate_detection_status
    if status == "complete":
        return [
            f"  extra_rows: {report.duplicate_extra_rows}",
            f"  distinct_rows_with_copies: {report.duplicate_distinct_rows}",
            f"  repeated_id_extra_rows: {report.repeated_id_extra_rows}",
            f"  repeated_id_distinct_rows: {report.repeated_id_distinct_rows}",
        ]
    lines = [
        "  extra_rows: unavailable",
        "  distinct_rows_with_copies: unavailable",
        "  repeated_id_extra_rows: unavailable",
        "  repeated_id_distinct_rows: unavailable",
    ]
    if status == "incomplete":
        lines.append("  Duplicate totals are unavailable because the fingerprint limit was reached.")
    elif status == "disabled":
        lines.append("  Duplicate detection was not run.")
    else:
        lines.append("  The body was not scanned. Duplicate results are unavailable.")
    return lines


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


def _timestamp(clock: object | None) -> datetime:
    if clock is None:
        return datetime.now(timezone.utc)
    if not callable(clock):
        raise TypeError("clock must be callable.")
    value = clock()
    if not isinstance(value, datetime):
        raise TypeError("clock must return a datetime.")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
