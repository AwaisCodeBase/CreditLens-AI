"""Contract tests for the synthetic labeled-client fixture.

The fixture is invented. These tests must not open either real dataset.
xlrd can read a legacy workbook but cannot write one, and this project does
not add a writer dependency. Workbook success paths use an in-memory sheet.
A temporary non-workbook file covers the unreadable-file path.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
import xlrd

from creditlens_contracts.default_clients import (
    CODED_HEADER,
    COLUMNS,
    EXPECTED_COLUMN_NAMES,
    SCHEMA_CONTRACT_VERSION,
    DefaultClientRecord,
    LabelSchemaError,
    parse_row,
    read_default_clients_csv,
    read_default_clients_xls,
    validate_header,
)
from creditlens_contracts.schema import EXPECTED_COLUMN_NAMES as TRANSACTION_HEADERS

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "synthetic_default_clients.csv"
PLANTED_ID = "991122"
PLANTED_AMOUNT = "445566.77"


def _records(path: Path) -> list[DefaultClientRecord]:
    return list(read_default_clients_csv(path))


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


def _valid_row(**overrides: str) -> dict[str, str]:
    values = {
        "ID": "7",
        "LIMIT_BAL": "500.00",
        "SEX": "1",
        "EDUCATION": "2",
        "MARRIAGE": "1",
        "AGE": "29",
        "PAY_0": "-1",
        "PAY_2": "-1",
        "PAY_3": "0",
        "PAY_4": "0",
        "PAY_5": "0",
        "PAY_6": "2",
        "BILL_AMT1": "-12.50",
        "BILL_AMT2": "0",
        "BILL_AMT3": "10",
        "BILL_AMT4": "0",
        "BILL_AMT5": "0",
        "BILL_AMT6": "0",
        "PAY_AMT1": "0",
        "PAY_AMT2": "20",
        "PAY_AMT3": "0",
        "PAY_AMT4": "0",
        "PAY_AMT5": "0",
        "PAY_AMT6": "0",
        "default payment next month": "0",
    }
    values.update(overrides)
    return values


class _Cell:
    def __init__(self, ctype: int, value: object) -> None:
        self.ctype = ctype
        self.value = value


class _Sheet:
    def __init__(self, rows: list[list[_Cell]]) -> None:
        self.nrows = len(rows)
        self.ncols = len(rows[0]) if rows else 0
        self._rows = rows

    def cell(self, row_index: int, column_index: int) -> _Cell:
        return self._rows[row_index][column_index]


class _Book:
    def __init__(self, sheet: _Sheet, names: tuple[str, ...] = ("Data",)) -> None:
        self._sheet = sheet
        self._names = names
        self.released = False

    def sheet_names(self) -> list[str]:
        return list(self._names)

    def sheet_by_name(self, name: str) -> _Sheet:
        if name != "Data" or "Data" not in self._names:
            raise xlrd.XLRDError("sheet")
        return self._sheet

    def release_resources(self) -> None:
        self.released = True


def _text_row(values: list[str]) -> list[_Cell]:
    return [_Cell(xlrd.XL_CELL_TEXT, value) for value in values]


def _coded_row() -> list[_Cell]:
    cells: list[_Cell] = []
    for label in CODED_HEADER:
        if label == "":
            cells.append(_Cell(xlrd.XL_CELL_EMPTY, ""))
        else:
            cells.append(_Cell(xlrd.XL_CELL_TEXT, label))
    return cells


def _number_row(values: list[float]) -> list[_Cell]:
    return [_Cell(xlrd.XL_CELL_NUMBER, value) for value in values]


_SAMPLE_NUMBERS = [
    7,
    500,
    1,
    2,
    1,
    29,
    -1,
    -1,
    0,
    0,
    0,
    2,
    -12.5,
    0,
    10,
    0,
    0,
    0,
    0,
    20,
    0,
    0,
    0,
    0,
    0,
]


def _open_book(monkeypatch: pytest.MonkeyPatch, book: _Book, path: Path) -> None:
    def open_workbook(filename: Path, **kwargs: object) -> _Book:
        assert Path(filename) == path
        assert kwargs["on_demand"] is True
        assert kwargs["formatting_info"] is False
        return book

    monkeypatch.setattr(
        "creditlens_contracts.default_clients.xlrd.open_workbook",
        open_workbook,
    )


def test_schema_documents_twenty_five_columns_in_order() -> None:
    assert SCHEMA_CONTRACT_VERSION == "1"
    assert len(COLUMNS) == 25
    assert EXPECTED_COLUMN_NAMES[-1] == "default payment next month"
    assert "PAY_1" not in EXPECTED_COLUMN_NAMES
    assert len(CODED_HEADER) == 25
    assert CODED_HEADER[0] == ""
    assert CODED_HEADER[-1] == "Y"
    record_fields = tuple(column.field for column in COLUMNS)
    assert record_fields[-1] == "default_payment_next_month"
    assert EXPECTED_COLUMN_NAMES != TRANSACTION_HEADERS


def test_fixture_header_parses_invented_records() -> None:
    reader = read_default_clients_csv(FIXTURE_PATH)
    assert not isinstance(reader, list)
    records = list(reader)
    assert FIXTURE_PATH.name != "default of credit card clients.xls"
    assert len(records) == 2
    assert records[0].ID == 7
    assert records[0].LIMIT_BAL == Decimal("500.00")
    assert records[0].default_payment_next_month == 0
    assert records[1].default_payment_next_month == 1
    assert isinstance(records[0].LIMIT_BAL, Decimal)
    assert not isinstance(records[0].LIMIT_BAL, float)


def test_reordered_header_is_rejected(tmp_path: Path) -> None:
    header = list(EXPECTED_COLUMN_NAMES)
    header[0], header[1] = header[1], header[0]
    path = tmp_path / "reordered.csv"
    _write_csv(path, [header])
    with pytest.raises(LabelSchemaError) as exc_info:
        _records(path)
    assert exc_info.value.code == "header:mismatch"


def test_missing_and_extra_header_columns_are_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "missing.csv"
    _write_csv(missing, [list(EXPECTED_COLUMN_NAMES[:-1])])
    with pytest.raises(LabelSchemaError) as missing_info:
        _records(missing)
    assert missing_info.value.code == "header:mismatch"

    extra = tmp_path / "extra.csv"
    _write_csv(extra, [[*EXPECTED_COLUMN_NAMES, "EXTRA"]])
    with pytest.raises(LabelSchemaError) as extra_info:
        _records(extra)
    assert extra_info.value.code == "header:mismatch"

    with pytest.raises(LabelSchemaError) as row_missing:
        parse_row({"ID": "7"})
    assert row_missing.value.code == "row:missing_columns"
    row = _valid_row()
    row["EXTRA"] = "1"
    with pytest.raises(LabelSchemaError) as row_extra:
        parse_row(row)
    assert row_extra.value.code == "row:unexpected_columns"


def test_coded_header_is_skipped_when_the_real_header_follows(tmp_path: Path) -> None:
    path = tmp_path / "coded.csv"
    _write_csv(
        path,
        [list(CODED_HEADER), list(EXPECTED_COLUMN_NAMES), list(_valid_row().values())],
    )
    records = _records(path)
    assert len(records) == 1
    assert records[0].ID == 7
    assert records[0].default_payment_next_month == 0


def test_coded_header_without_real_header_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "coded-only.csv"
    _write_csv(path, [list(CODED_HEADER)])
    with pytest.raises(LabelSchemaError) as exc_info:
        _records(path)
    assert exc_info.value.code == "header:missing"


def test_empty_file_and_header_only_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(LabelSchemaError) as exc_info:
        _records(empty)
    assert exc_info.value.code == "header:missing"

    header_only = tmp_path / "header.csv"
    _write_csv(header_only, [list(EXPECTED_COLUMN_NAMES)])
    assert _records(header_only) == []


def test_blank_target_is_rejected() -> None:
    with pytest.raises(LabelSchemaError) as exc_info:
        parse_row(_valid_row(**{"default payment next month": "   "}))
    assert exc_info.value.code == "target:empty"


def test_target_two_one_and_a_half_and_text_are_rejected() -> None:
    for raw in ("2", "1.5", "defaulted", "yes", "00"):
        with pytest.raises(LabelSchemaError) as exc_info:
            parse_row(_valid_row(**{"default payment next month": raw}))
        assert exc_info.value.code == "target:invalid"


def test_invalid_numeric_field_uses_its_column_code() -> None:
    with pytest.raises(LabelSchemaError) as amount_info:
        parse_row(_valid_row(LIMIT_BAL="NOT_A_NUMBER"))
    assert amount_info.value.code == "LIMIT_BAL:unparseable"

    with pytest.raises(LabelSchemaError) as status_info:
        parse_row(_valid_row(PAY_0="1.5"))
    assert status_info.value.code == "PAY_0:unparseable"

    with pytest.raises(LabelSchemaError) as empty_info:
        parse_row(_valid_row(ID=""))
    assert empty_info.value.code == "ID:empty"

    with pytest.raises(LabelSchemaError) as id_info:
        parse_row(_valid_row(ID="ABC"))
    assert id_info.value.code == "ID:unparseable"

    with pytest.raises(LabelSchemaError) as limit_info:
        parse_row(_valid_row(LIMIT_BAL=""))
    assert limit_info.value.code == "LIMIT_BAL:empty"


def test_non_ascii_digits_are_rejected() -> None:
    non_ascii_digits = "١٢٣"
    with pytest.raises(LabelSchemaError) as exc_info:
        parse_row(_valid_row(ID=non_ascii_digits))
    assert exc_info.value.code == "ID:unparseable"
    assert non_ascii_digits not in str(exc_info.value)


def test_negative_bill_and_repayment_status_are_preserved() -> None:
    record = _records(FIXTURE_PATH)[0]
    assert record.PAY_0 == -1
    assert record.BILL_AMT1 == Decimal("-12.50")
    assert _records(FIXTURE_PATH)[1].PAY_5 == -2


def test_undocumented_category_codes_are_not_rewritten() -> None:
    record = parse_row(_valid_row(EDUCATION="6", MARRIAGE="0", PAY_6="9"))
    assert record.EDUCATION == 6
    assert record.MARRIAGE == 0
    assert record.PAY_6 == 9


def test_error_text_omits_planted_identifier_and_amount() -> None:
    with pytest.raises(LabelSchemaError) as exc_info:
        parse_row(
            _valid_row(
                ID=PLANTED_ID,
                LIMIT_BAL=PLANTED_AMOUNT,
                **{"default payment next month": "2"},
            )
        )
    message = str(exc_info.value)
    assert exc_info.value.code == "target:invalid"
    assert PLANTED_ID not in message
    assert PLANTED_AMOUNT not in message
    assert "2" not in message


def test_transaction_header_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "transactions.csv"
    _write_csv(path, [list(TRANSACTION_HEADERS), ["1"] * len(TRANSACTION_HEADERS)])
    with pytest.raises(LabelSchemaError) as exc_info:
        _records(path)
    assert exc_info.value.code == "header:mismatch"


def test_xls_reader_skips_coded_header_and_preserves_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sheet = _Sheet(
        [
            _coded_row(),
            _text_row(list(EXPECTED_COLUMN_NAMES)),
            _number_row(_SAMPLE_NUMBERS),
        ]
    )
    book = _Book(sheet)
    path = tmp_path / "synthetic-clients.xls"
    path.write_bytes(b"placeholder")
    _open_book(monkeypatch, book, path)

    reader = read_default_clients_xls(path)
    assert not isinstance(reader, list)
    records = list(reader)
    assert book.released
    assert len(records) == 1
    assert records[0].ID == 7
    assert records[0].PAY_0 == -1
    assert records[0].BILL_AMT1 == Decimal("-12.5")
    assert records[0].default_payment_next_month == 0


def test_xls_reader_accepts_a_real_header_without_a_coded_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sheet = _Sheet([_text_row(list(EXPECTED_COLUMN_NAMES))])
    book = _Book(sheet)
    path = tmp_path / "header-only.xls"
    path.write_bytes(b"placeholder")
    _open_book(monkeypatch, book, path)
    assert list(read_default_clients_xls(path)) == []
    assert book.released


def test_xls_reader_rejects_other_sheets_and_bad_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrong-sheet.xls"
    path.write_bytes(b"placeholder")
    book = _Book(_Sheet([]), names=("Notes",))
    _open_book(monkeypatch, book, path)
    with pytest.raises(LabelSchemaError) as sheet_info:
        list(read_default_clients_xls(path))
    assert sheet_info.value.code == "sheet:mismatch"
    assert book.released
    assert PLANTED_AMOUNT not in str(sheet_info.value)

    numbers = list(_SAMPLE_NUMBERS)
    numbers[-1] = 1.5
    target_sheet = _Sheet(
        [_text_row(list(EXPECTED_COLUMN_NAMES)), _number_row(numbers)]
    )
    target_book = _Book(target_sheet)
    _open_book(monkeypatch, target_book, path)
    with pytest.raises(LabelSchemaError) as target_info:
        list(read_default_clients_xls(path))
    assert target_info.value.code == "target:invalid"
    assert "1.5" not in str(target_info.value)

    text_target = _number_row(_SAMPLE_NUMBERS)
    text_target[-1] = _Cell(xlrd.XL_CELL_TEXT, "yes")
    text_sheet = _Sheet([_text_row(list(EXPECTED_COLUMN_NAMES)), text_target])
    text_book = _Book(text_sheet)
    _open_book(monkeypatch, text_book, path)
    with pytest.raises(LabelSchemaError) as text_info:
        list(read_default_clients_xls(path))
    assert text_info.value.code == "target:invalid"
    assert "yes" not in str(text_info.value)


def test_xls_reader_rejects_a_file_that_is_not_a_workbook(tmp_path: Path) -> None:
    path = tmp_path / "notes.xls"
    path.write_bytes(b"this is not a workbook")
    with pytest.raises(LabelSchemaError) as exc_info:
        list(read_default_clients_xls(path))
    assert exc_info.value.code == "workbook:unreadable"
    assert "this is not a workbook" not in str(exc_info.value)


def test_validate_header_accepts_only_the_real_order() -> None:
    validate_header(EXPECTED_COLUMN_NAMES)
    with pytest.raises(LabelSchemaError) as exc_info:
        validate_header(CODED_HEADER)
    assert exc_info.value.code == "header:mismatch"
