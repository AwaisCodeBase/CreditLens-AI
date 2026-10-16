"""Contract tests for the synthetic transaction fixture.

The fixture is invented. These tests must not open the original extract.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from creditlens_contracts.schema import (
    COLUMNS,
    EXPECTED_COLUMN_NAMES,
    ColumnRole,
    SchemaError,
    read_transaction_csv,
    validate_header,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "synthetic_transactions.csv"
ORIGINAL_EXTRACT = "AI_CREDIT_SCORING_TABLE.csv"


def test_schema_documents_seventeen_columns_in_order() -> None:
    assert len(COLUMNS) == 17
    assert len(EXPECTED_COLUMN_NAMES) == len(set(EXPECTED_COLUMN_NAMES))
    assert EXPECTED_COLUMN_NAMES == (
        "SUBSCRIBERID",
        "TRANSACTIONDATE",
        "TRANSFERAMOUNT",
        "CHARGEAMOUNT",
        "SERVICENAME",
        "SHORTNAME",
        "PREVIOUSBALANCE",
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


def test_fixture_header_matches_documented_order() -> None:
    records = read_transaction_csv(FIXTURE_PATH)
    assert len(records) == 4
    assert FIXTURE_PATH.name != ORIGINAL_EXTRACT


def test_fixture_has_no_unexpected_columns() -> None:
    header = FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert tuple(header) == EXPECTED_COLUMN_NAMES
    assert set(header) == set(EXPECTED_COLUMN_NAMES)


def test_transaction_dates_parse_as_timestamps() -> None:
    records = read_transaction_csv(FIXTURE_PATH)
    assert records[0].TRANSACTIONDATE == datetime(2020, 3, 14, 9, 15, 0)
    assert records[2].TRANSACTIONDATE == datetime(2020, 6, 1)
    assert all(isinstance(record.TRANSACTIONDATE, datetime) for record in records)


def test_amount_and_balance_fields_are_decimals() -> None:
    record = read_transaction_csv(FIXTURE_PATH)[0]
    assert isinstance(record.TRANSFERAMOUNT, Decimal)
    assert isinstance(record.CHARGEAMOUNT, Decimal)
    assert isinstance(record.PREVIOUSBALANCE, Decimal)
    assert not isinstance(record.TRANSFERAMOUNT, float)
    assert record.TRANSFERAMOUNT == Decimal("25.50")
    assert record.CHARGEAMOUNT == Decimal("0.00")
    assert record.PREVIOUSBALANCE == Decimal("100.00")

    amount_columns = tuple(
        column.name for column in COLUMNS if column.role is ColumnRole.AMOUNT
    )
    assert amount_columns == ("TRANSFERAMOUNT", "CHARGEAMOUNT", "PREVIOUSBALANCE")


def test_subscriber_identifiers_stay_text() -> None:
    records = read_transaction_csv(FIXTURE_PATH)
    assert isinstance(records[0].SUBSCRIBERID, str)
    assert records[0].SUBSCRIBERID == "00123"
    assert records[0].SUBSCRIBERID != str(int(records[0].SUBSCRIBERID))
    assert records[2].SUBSCRIBERID == "SUB-9"
    assert records[3].SUBSCRIBERID == "0007"

    identifier_columns = tuple(
        column.name for column in COLUMNS if column.role is ColumnRole.IDENTIFIER
    )
    assert identifier_columns == ("SUBSCRIBERID",)


def test_blank_age_is_allowed_by_the_schema() -> None:
    records = read_transaction_csv(FIXTURE_PATH)
    assert records[1].AGE is None
    assert records[0].AGE == Decimal("34")


def test_reordered_or_extra_header_is_rejected() -> None:
    reordered = list(EXPECTED_COLUMN_NAMES)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    with pytest.raises(SchemaError):
        validate_header(reordered)

    with pytest.raises(SchemaError):
        validate_header([*EXPECTED_COLUMN_NAMES, "UNEXPECTED"])
