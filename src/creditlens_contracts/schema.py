"""Column contract for the CreditLens transaction extract.

This module is the only definition of the 17-column schema. It checks
column names, column order, and whether values can be parsed into the
types below. Range checks and other data-quality rules belong in a
later phase and are intentionally not applied here.

Attribute names on ``TransactionRecord`` match the file header so the
contract uses one vocabulary.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Mapping

TIMESTAMP_FORMATS: tuple[str, ...] = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


class ColumnRole(Enum):
    """How a column is parsed. This is not a data-quality judgement."""

    IDENTIFIER = "identifier"
    TIMESTAMP = "timestamp"
    AMOUNT = "amount"
    OPTIONAL_DECIMAL = "optional_decimal"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str
    role: ColumnRole
    description: str


# Documented header order for AI_CREDIT_SCORING_TABLE.csv.
COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(
        "SUBSCRIBERID",
        ColumnRole.IDENTIFIER,
        "Person identifier. Kept as text so leading zeros are preserved.",
    ),
    ColumnSpec(
        "TRANSACTIONDATE",
        ColumnRole.TIMESTAMP,
        "Transaction timestamp. Accepted as YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.",
    ),
    ColumnSpec(
        "TRANSFERAMOUNT",
        ColumnRole.AMOUNT,
        "Transfer amount as a decimal number.",
    ),
    ColumnSpec(
        "CHARGEAMOUNT",
        ColumnRole.AMOUNT,
        "Fee amount as a decimal number.",
    ),
    ColumnSpec(
        "SERVICENAME",
        ColumnRole.TEXT,
        "Name of the wallet service.",
    ),
    ColumnSpec(
        "SHORTNAME",
        ColumnRole.TEXT,
        "Currency code.",
    ),
    ColumnSpec(
        "PREVIOUSBALANCE",
        ColumnRole.AMOUNT,
        "Balance before the transaction, as a decimal number.",
    ),
    ColumnSpec(
        "AGE",
        ColumnRole.OPTIONAL_DECIMAL,
        "Age as a decimal when present. Blank is still valid at the schema layer.",
    ),
    ColumnSpec("GENDER", ColumnRole.TEXT, "Gender text. May be blank."),
    ColumnSpec(
        "EDUCATIONLEVELNAME",
        ColumnRole.TEXT,
        "Education label. May be blank.",
    ),
    ColumnSpec(
        "MONTHLYINCOMERANGE",
        ColumnRole.TEXT,
        "Income-range label. May be blank.",
    ),
    ColumnSpec("JOBINFORMATION", ColumnRole.TEXT, "Job text. May be blank."),
    ColumnSpec("SUBCITY", ColumnRole.TEXT, "Subcity text. May be blank."),
    ColumnSpec("SECTOR", ColumnRole.TEXT, "Sector text. May be blank."),
    ColumnSpec("CITYNAME", ColumnRole.TEXT, "City text. May be blank."),
    ColumnSpec("STATENAME", ColumnRole.TEXT, "State text. May be blank."),
    ColumnSpec("COUNTRYNAME", ColumnRole.TEXT, "Country text. May be blank."),
)

EXPECTED_COLUMN_NAMES: tuple[str, ...] = tuple(column.name for column in COLUMNS)


class SchemaError(ValueError):
    """The file does not match the transaction schema."""


@dataclass(frozen=True, slots=True)
class TransactionRecord:
    SUBSCRIBERID: str
    TRANSACTIONDATE: datetime
    TRANSFERAMOUNT: Decimal
    CHARGEAMOUNT: Decimal
    SERVICENAME: str
    SHORTNAME: str
    PREVIOUSBALANCE: Decimal
    AGE: Decimal | None
    GENDER: str
    EDUCATIONLEVELNAME: str
    MONTHLYINCOMERANGE: str
    JOBINFORMATION: str
    SUBCITY: str
    SECTOR: str
    CITYNAME: str
    STATENAME: str
    COUNTRYNAME: str


def _record_fields_match_schema() -> None:
    record_fields = tuple(field.name for field in fields(TransactionRecord))
    if record_fields != EXPECTED_COLUMN_NAMES:
        raise SchemaError(
            "TransactionRecord fields do not match the documented column order."
        )


_record_fields_match_schema()


def validate_header(header: list[str] | tuple[str, ...]) -> None:
    """Raise if the header is missing, reordered, or has extra columns."""

    names = tuple(header)
    if names != EXPECTED_COLUMN_NAMES:
        raise SchemaError(
            "CSV header does not match the documented 17-column order. "
            f"Expected {list(EXPECTED_COLUMN_NAMES)}."
        )


def parse_row(row: Mapping[str, str | None]) -> TransactionRecord:
    """Parse one raw CSV row into schema types."""

    unexpected = [key for key in row if key not in EXPECTED_COLUMN_NAMES]
    if unexpected:
        raise SchemaError(f"Unexpected columns: {unexpected}")

    missing = [name for name in EXPECTED_COLUMN_NAMES if name not in row]
    if missing:
        raise SchemaError(f"Missing columns: {missing}")

    parsed: dict[str, object] = {}
    for column in COLUMNS:
        raw = _cell(row[column.name])
        parsed[column.name] = _parse_cell(column, raw)
    return TransactionRecord(**parsed)  # type: ignore[arg-type]


def read_transaction_csv(path: Path) -> list[TransactionRecord]:
    """Read a transaction CSV that uses the documented header."""

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SchemaError("CSV has no header row.")
        validate_header(list(reader.fieldnames))
        return [parse_row(row) for row in reader]


def _cell(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def _parse_cell(column: ColumnSpec, raw: str) -> str | datetime | Decimal | None:
    if column.role is ColumnRole.IDENTIFIER:
        if raw == "":
            raise SchemaError(f"{column.name} is empty.")
        return raw
    if column.role is ColumnRole.TIMESTAMP:
        return _parse_timestamp(raw, column.name)
    if column.role is ColumnRole.AMOUNT:
        return _parse_decimal(raw, column.name)
    if column.role is ColumnRole.OPTIONAL_DECIMAL:
        if raw == "":
            return None
        return _parse_decimal(raw, column.name)
    if column.role is ColumnRole.TEXT:
        return raw
    raise SchemaError(f"No parser for {column.name}.")


def _parse_timestamp(raw: str, column_name: str) -> datetime:
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise SchemaError(
        f"{column_name} is not a timestamp in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS."
    )


def _parse_decimal(raw: str, column_name: str) -> Decimal:
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise SchemaError(f"{column_name} is not a decimal number.") from exc
    if not number.is_finite():
        raise SchemaError(f"{column_name} is not a finite decimal number.")
    return number
