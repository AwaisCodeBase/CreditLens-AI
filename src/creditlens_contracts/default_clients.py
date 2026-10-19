"""Column contract for the UCI Default of Credit Card Clients workbook.

This module is separate from the 17-column transaction contract in
``schema.py``. It checks the 25 real column names, their order, and whether
each cell can be parsed. It does not judge whether a category code is
documented, and it does not train a model.

The Python attribute for the target is ``default_payment_next_month``.
The file header remains ``default payment next month``.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path

import xlrd
from xlrd.compdoc import CompDocError

# Bump this when column names, column order, or parse rules change.
SCHEMA_CONTRACT_VERSION: str = "1"

_INTEGER_TEXT = re.compile(r"-?[0-9]+")


class ColumnRole(Enum):
    """How a column is parsed. This is not a data-quality judgement."""

    INTEGER = "integer"
    DECIMAL = "decimal"
    TARGET = "target"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    header: str
    field: str
    role: ColumnRole
    description: str


# Real header order confirmed from the local workbook's second row.
COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("ID", "ID", ColumnRole.INTEGER, "Client identifier, stored as an integer."),
    ColumnSpec(
        "LIMIT_BAL",
        "LIMIT_BAL",
        ColumnRole.DECIMAL,
        "Credit limit, stored as a decimal number.",
    ),
    ColumnSpec("SEX", "SEX", ColumnRole.INTEGER, "Sex code, stored as an integer."),
    ColumnSpec(
        "EDUCATION",
        "EDUCATION",
        ColumnRole.INTEGER,
        "Education code, stored as an integer.",
    ),
    ColumnSpec(
        "MARRIAGE",
        "MARRIAGE",
        ColumnRole.INTEGER,
        "Marriage code, stored as an integer.",
    ),
    ColumnSpec("AGE", "AGE", ColumnRole.INTEGER, "Age in years, stored as an integer."),
    ColumnSpec("PAY_0", "PAY_0", ColumnRole.INTEGER, "Repayment status, stored as an integer."),
    ColumnSpec("PAY_2", "PAY_2", ColumnRole.INTEGER, "Repayment status, stored as an integer."),
    ColumnSpec("PAY_3", "PAY_3", ColumnRole.INTEGER, "Repayment status, stored as an integer."),
    ColumnSpec("PAY_4", "PAY_4", ColumnRole.INTEGER, "Repayment status, stored as an integer."),
    ColumnSpec("PAY_5", "PAY_5", ColumnRole.INTEGER, "Repayment status, stored as an integer."),
    ColumnSpec("PAY_6", "PAY_6", ColumnRole.INTEGER, "Repayment status, stored as an integer."),
    ColumnSpec("BILL_AMT1", "BILL_AMT1", ColumnRole.DECIMAL, "Bill amount as a decimal number."),
    ColumnSpec("BILL_AMT2", "BILL_AMT2", ColumnRole.DECIMAL, "Bill amount as a decimal number."),
    ColumnSpec("BILL_AMT3", "BILL_AMT3", ColumnRole.DECIMAL, "Bill amount as a decimal number."),
    ColumnSpec("BILL_AMT4", "BILL_AMT4", ColumnRole.DECIMAL, "Bill amount as a decimal number."),
    ColumnSpec("BILL_AMT5", "BILL_AMT5", ColumnRole.DECIMAL, "Bill amount as a decimal number."),
    ColumnSpec("BILL_AMT6", "BILL_AMT6", ColumnRole.DECIMAL, "Bill amount as a decimal number."),
    ColumnSpec("PAY_AMT1", "PAY_AMT1", ColumnRole.DECIMAL, "Payment amount as a decimal number."),
    ColumnSpec("PAY_AMT2", "PAY_AMT2", ColumnRole.DECIMAL, "Payment amount as a decimal number."),
    ColumnSpec("PAY_AMT3", "PAY_AMT3", ColumnRole.DECIMAL, "Payment amount as a decimal number."),
    ColumnSpec("PAY_AMT4", "PAY_AMT4", ColumnRole.DECIMAL, "Payment amount as a decimal number."),
    ColumnSpec("PAY_AMT5", "PAY_AMT5", ColumnRole.DECIMAL, "Payment amount as a decimal number."),
    ColumnSpec("PAY_AMT6", "PAY_AMT6", ColumnRole.DECIMAL, "Payment amount as a decimal number."),
    ColumnSpec(
        "default payment next month",
        "default_payment_next_month",
        ColumnRole.TARGET,
        "1 means default next month. 0 means no default.",
    ),
)

EXPECTED_COLUMN_NAMES: tuple[str, ...] = tuple(column.header for column in COLUMNS)
_EXPECTED_HEADERS = set(EXPECTED_COLUMN_NAMES)

# The local workbook's first row: an empty ID cell, then X1..X23, then Y.
CODED_HEADER: tuple[str, ...] = ("",) + tuple(f"X{index}" for index in range(1, 24)) + ("Y",)


class LabelSchemaError(ValueError):
    """The file does not match the labeled-client schema.

    ``code`` is a stable identifier. The message does not include cell values.
    Callers that classify failures must use ``code``, not the message text.
    """

    code: str

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DefaultClientRecord:
    ID: int
    LIMIT_BAL: Decimal
    SEX: int
    EDUCATION: int
    MARRIAGE: int
    AGE: int
    PAY_0: int
    PAY_2: int
    PAY_3: int
    PAY_4: int
    PAY_5: int
    PAY_6: int
    BILL_AMT1: Decimal
    BILL_AMT2: Decimal
    BILL_AMT3: Decimal
    BILL_AMT4: Decimal
    BILL_AMT5: Decimal
    BILL_AMT6: Decimal
    PAY_AMT1: Decimal
    PAY_AMT2: Decimal
    PAY_AMT3: Decimal
    PAY_AMT4: Decimal
    PAY_AMT5: Decimal
    PAY_AMT6: Decimal
    default_payment_next_month: int


def _record_fields_match_schema() -> None:
    record_fields = tuple(field.name for field in fields(DefaultClientRecord))
    expected_fields = tuple(column.field for column in COLUMNS)
    if record_fields != expected_fields:
        raise LabelSchemaError(
            "DefaultClientRecord fields do not match the documented column order.",
            code="record:fields_mismatch",
        )


_record_fields_match_schema()


def validate_header(header: list[str] | tuple[str, ...]) -> None:
    """Raise if the real header is missing a name, reordered, or extended."""

    names = tuple("" if item is None else str(item).strip() for item in header)
    if names != EXPECTED_COLUMN_NAMES:
        raise LabelSchemaError(
            "Header does not match the documented 25-column order.",
            code="header:mismatch",
        )


def parse_row(row: Mapping[str, str | None]) -> DefaultClientRecord:
    """Parse one CSV row keyed by the real header names."""

    if any(key not in _EXPECTED_HEADERS for key in row):
        raise LabelSchemaError(
            "The row contains columns that are not in the contract.",
            code="row:unexpected_columns",
        )
    if any(name not in row for name in EXPECTED_COLUMN_NAMES):
        raise LabelSchemaError(
            "The row is missing required columns.",
            code="row:missing_columns",
        )

    parsed: dict[str, int | Decimal] = {}
    for column in COLUMNS:
        parsed[column.field] = _parse_text_field(column, _cell(row[column.header]))
    return DefaultClientRecord(**parsed)  # type: ignore[arg-type]


def read_default_clients_csv(path: Path) -> Iterator[DefaultClientRecord]:
    """Yield records from a CSV without building a list of them.

    The first row may be the real 25-column header. It may instead be the
    coded header ``"" , X1, X2, ... X23, Y``, followed by the real header.
    Any other first row is rejected before a data row is parsed. The file
    is opened for reading only. Close or exhaust the generator so the
    handle closes.
    """

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            first = next(reader)
        except StopIteration:
            raise LabelSchemaError("CSV has no header row.", code="header:missing") from None
        header = _resolve_csv_header(first, reader)
        validate_header(header)
        for raw in reader:
            yield parse_row(_csv_data_row(header, raw))


def read_default_clients_xls(path: Path) -> Iterator[DefaultClientRecord]:
    """Yield records from the ``Data`` sheet of a legacy ``.xls`` workbook.

    ``xlrd`` reads the workbook into memory. This function then yields one
    record at a time and does not build a second list. The file is opened
    for reading only. A coded-header row is skipped only when it is exactly
    the inspected ``X1`` ... ``Y`` row and the next row is the real header.
    """

    try:
        book = xlrd.open_workbook(path, on_demand=True, formatting_info=False)
    except (xlrd.XLRDError, CompDocError) as exc:
        raise LabelSchemaError(
            "Workbook could not be read.",
            code="workbook:unreadable",
        ) from exc
    try:
        yield from _iter_xls_book(book)
    finally:
        book.release_resources()


def _resolve_csv_header(
    first: list[str],
    reader: Iterator[list[str]],
) -> tuple[str, ...]:
    labels = _stripped(first)
    if labels == CODED_HEADER:
        try:
            second = next(reader)
        except StopIteration:
            raise LabelSchemaError("CSV has no header row.", code="header:missing") from None
        return _stripped(second)
    return labels


def _csv_data_row(header: Sequence[str], raw: list[str]) -> dict[str, str]:
    labels = _stripped(raw)
    if len(labels) > len(header):
        raise LabelSchemaError(
            "The row contains columns that are not in the contract.",
            code="row:unexpected_columns",
        )
    if len(labels) < len(header):
        raise LabelSchemaError(
            "The row is missing required columns.",
            code="row:missing_columns",
        )
    return dict(zip(header, labels, strict=True))


def _stripped(raw: Sequence[str]) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in raw)


def _iter_xls_book(book: xlrd.Book) -> Iterator[DefaultClientRecord]:
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
        yield _parse_xls_row(sheet, row_index)


def _xls_data_start(sheet: xlrd.sheet.Sheet) -> int:
    first = _xls_row_text(sheet, 0)
    if first == CODED_HEADER:
        if sheet.nrows < 2:
            raise LabelSchemaError("Workbook has no header row.", code="header:missing")
        second = _xls_row_text(sheet, 1)
        if second is None:
            raise LabelSchemaError(
                "Header does not match the documented 25-column order.",
                code="header:mismatch",
            )
        validate_header(second)
        return 2
    if first is None:
        raise LabelSchemaError(
            "Header does not match the documented 25-column order.",
            code="header:mismatch",
        )
    validate_header(first)
    return 1


def _xls_row_text(sheet: xlrd.sheet.Sheet, row_index: int) -> tuple[str, ...] | None:
    if sheet.ncols != len(EXPECTED_COLUMN_NAMES):
        return None
    labels: list[str] = []
    for column_index in range(sheet.ncols):
        cell = sheet.cell(row_index, column_index)
        if cell.ctype == xlrd.XL_CELL_TEXT:
            labels.append(str(cell.value).strip())
        elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
            labels.append("")
        else:
            return None
    return tuple(labels)


def _parse_xls_row(sheet: xlrd.sheet.Sheet, row_index: int) -> DefaultClientRecord:
    parsed: dict[str, int | Decimal] = {}
    for index, column in enumerate(COLUMNS):
        parsed[column.field] = _parse_xls_cell(column, sheet.cell(row_index, index))
    return DefaultClientRecord(**parsed)  # type: ignore[arg-type]


def _parse_xls_cell(column: ColumnSpec, cell: xlrd.sheet.Cell) -> int | Decimal:
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return _parse_text_field(column, "")
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return _parse_text_field(column, str(cell.value).strip())
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return _parse_number_field(column, float(cell.value))
    raise _bad_value(column)


def _parse_text_field(column: ColumnSpec, raw: str) -> int | Decimal:
    if column.role is ColumnRole.TARGET:
        if raw == "":
            raise LabelSchemaError("The target is empty.", code="target:empty")
        if raw not in {"0", "1"}:
            raise LabelSchemaError("The target is not 0 or 1.", code="target:invalid")
        return int(raw)
    if raw == "":
        raise LabelSchemaError(f"{column.header} is empty.", code=f"{column.header}:empty")
    if column.role is ColumnRole.INTEGER:
        if _INTEGER_TEXT.fullmatch(raw) is None:
            raise _bad_value(column)
        return int(raw)
    if column.role is ColumnRole.DECIMAL:
        return _parse_decimal(raw, column)
    raise _bad_value(column)


def _parse_number_field(column: ColumnSpec, value: float) -> int | Decimal:
    if column.role is ColumnRole.DECIMAL:
        if value != value or value in (float("inf"), float("-inf")):
            raise _bad_value(column)
        if value.is_integer():
            return Decimal(int(value))
        return Decimal(str(value))
    parsed_int = _exact_int(value)
    if column.role is ColumnRole.TARGET:
        if parsed_int not in (0, 1):
            raise LabelSchemaError("The target is not 0 or 1.", code="target:invalid")
        return parsed_int
    if parsed_int is None:
        raise _bad_value(column)
    return parsed_int


def _parse_decimal(raw: str, column: ColumnSpec) -> Decimal:
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise _bad_value(column) from exc
    if not number.is_finite():
        raise _bad_value(column)
    return number


def _exact_int(value: float) -> int | None:
    if value != value or value in (float("inf"), float("-inf")) or not value.is_integer():
        return None
    return int(value)


def _bad_value(column: ColumnSpec) -> LabelSchemaError:
    if column.role is ColumnRole.TARGET:
        return LabelSchemaError("The target is not 0 or 1.", code="target:invalid")
    if column.role is ColumnRole.INTEGER:
        return LabelSchemaError(
            f"{column.header} is not an integer.",
            code=f"{column.header}:unparseable",
        )
    return LabelSchemaError(
        f"{column.header} is not a decimal number.",
        code=f"{column.header}:unparseable",
    )


def _cell(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()
