"""Aggregate checks for the synthetic labeled-client fixture.

These tests must not open either real dataset. Workbook failures use a
temporary non-workbook file or an in-memory sheet.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import xlrd

from creditlens_contracts.default_clients import CODED_HEADER, EXPECTED_COLUMN_NAMES
from creditlens_contracts.default_clients_quality import (
    main,
    profile_default_clients,
    render_default_client_report,
)
from creditlens_contracts.schema import EXPECTED_COLUMN_NAMES as TRANSACTION_HEADERS

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quality_default_clients.csv"
PLANTED_ID = "991122"
PLANTED_AMOUNT = "8765432100000.01"


def _counts(items: tuple[tuple[str, int], ...] | None) -> dict[str, int]:
    assert items is not None
    return dict(items)


def _write(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


def _row(**overrides: str) -> list[str]:
    values = {
        "ID": "7",
        "LIMIT_BAL": "500.00",
        "SEX": "1",
        "EDUCATION": "2",
        "MARRIAGE": "1",
        "AGE": "29",
        "PAY_0": "-1",
        "PAY_2": "-1",
        "PAY_3": "-1",
        "PAY_4": "-1",
        "PAY_5": "-1",
        "PAY_6": "-1",
        "BILL_AMT1": "10",
        "BILL_AMT2": "0",
        "BILL_AMT3": "0",
        "BILL_AMT4": "0",
        "BILL_AMT5": "0",
        "BILL_AMT6": "0",
        "PAY_AMT1": "0",
        "PAY_AMT2": "0",
        "PAY_AMT3": "0",
        "PAY_AMT4": "0",
        "PAY_AMT5": "0",
        "PAY_AMT6": "0",
        "default payment next month": "0",
    }
    values.update(overrides)
    return [values[name] for name in EXPECTED_COLUMN_NAMES]


def _profile(path: Path, **kwargs: object):
    return profile_default_clients(path, **kwargs)  # type: ignore[arg-type]


def test_both_target_classes_are_counted_without_an_absent_warning() -> None:
    report = _profile(FIXTURE_PATH)
    assert report.body_scanned is True
    assert report.scan_status == "complete"
    assert report.quality_results_status == "complete"
    assert _counts(report.target_counts) == {"0": 2, "1": 1}
    assert "target_class_absent" not in report.warnings


def test_absent_target_class_is_warned(tmp_path: Path) -> None:
    path = tmp_path / "one-class.csv"
    _write(path, [list(EXPECTED_COLUMN_NAMES), _row(**{"default payment next month": "0"})])
    report = _profile(path)
    assert _counts(report.target_counts) == {"0": 1, "1": 0}
    assert "target_class_absent" in report.warnings


def test_undocumented_education_and_marriage_are_counted_not_corrected() -> None:
    report = _profile(FIXTURE_PATH)
    education = _counts(report.education_counts)
    marriage = _counts(report.marriage_counts)
    assert education["6"] == 1
    assert education["2"] == 2
    assert marriage["0"] == 1
    assert marriage["1"] == 2
    assert "undocumented_education_code" in report.warnings
    assert "undocumented_marriage_code" in report.warnings


def test_education_zero_and_five_are_undocumented(tmp_path: Path) -> None:
    path = tmp_path / "education.csv"
    _write(
        path,
        [
            list(EXPECTED_COLUMN_NAMES),
            _row(ID="7", EDUCATION="0"),
            _row(ID="8", EDUCATION="5", **{"default payment next month": "1"}),
        ],
    )
    report = _profile(path)
    education = _counts(report.education_counts)
    assert education["0"] == 1
    assert education["5"] == 1
    assert education["4"] == 0
    assert "undocumented_education_code" in report.warnings


def test_marriage_code_outside_zero_to_three_is_other(tmp_path: Path) -> None:
    path = tmp_path / "marriage.csv"
    _write(path, [list(EXPECTED_COLUMN_NAMES), _row(MARRIAGE="4")])
    report = _profile(path)
    marriage = _counts(report.marriage_counts)
    assert marriage["other"] == 1
    assert marriage["3"] == 0
    assert "undocumented_marriage_code" in report.warnings


def test_ages_outside_18_to_100_are_warned(tmp_path: Path) -> None:
    path = tmp_path / "ages.csv"
    _write(
        path,
        [
            list(EXPECTED_COLUMN_NAMES),
            _row(ID="7", AGE="17"),
            _row(ID="8", AGE="101", **{"default payment next month": "1"}),
        ],
    )
    report = _profile(path)
    rendered = render_default_client_report(report)
    bands = _counts(report.age_bands)
    assert bands["under_18"] == 1
    assert bands["over_100"] == 1
    assert bands["age_18_to_100"] == 0
    assert "age_outside_review_band" in report.warnings
    assert "17" not in rendered
    assert "101" not in rendered


def test_undocumented_sex_is_counted_and_warned() -> None:
    report = _profile(FIXTURE_PATH)
    assert _counts(report.sex_counts)["undocumented"] == 1
    assert _counts(report.sex_counts)["1"] == 2
    assert "undocumented_sex_code" in report.warnings


def test_repayment_minus_two_and_zero_are_counted_separately() -> None:
    report = _profile(FIXTURE_PATH)
    assert report.repayment_counts is not None
    counts = {column: dict(bins) for column, bins in report.repayment_counts}
    assert counts["PAY_0"]["-2"] == 1
    assert counts["PAY_0"]["-1"] == 2
    assert counts["PAY_2"]["0"] == 1
    assert counts["PAY_2"]["-1"] == 2
    assert "ambiguous_repayment_status" in report.warnings
    assert "undocumented_repayment_status" not in report.warnings


def test_ambiguous_repayment_codes_do_not_raise_undocumented(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous-pay.csv"
    _write(
        path,
        [
            list(EXPECTED_COLUMN_NAMES),
            _row(PAY_0="-2", PAY_2="0"),
        ],
    )
    report = _profile(path)
    assert report.repayment_counts is not None
    counts = {column: dict(bins) for column, bins in report.repayment_counts}
    assert counts["PAY_0"]["-2"] == 1
    assert counts["PAY_2"]["0"] == 1
    assert counts["PAY_0"]["other"] == 0
    assert "ambiguous_repayment_status" in report.warnings
    assert "undocumented_repayment_status" not in report.warnings


def test_repayment_code_outside_recognized_set_is_undocumented(tmp_path: Path) -> None:
    path = tmp_path / "unknown-pay.csv"
    _write(path, [list(EXPECTED_COLUMN_NAMES), _row(PAY_0="12")])
    report = _profile(path)
    rendered = render_default_client_report(report)
    assert report.repayment_counts is not None
    counts = {column: dict(bins) for column, bins in report.repayment_counts}
    assert counts["PAY_0"]["other"] == 1
    assert counts["PAY_0"]["-2"] == 0
    assert "undocumented_repayment_status" in report.warnings
    assert "ambiguous_repayment_status" not in report.warnings
    assert "\n    12:" not in rendered


def test_negative_bill_is_counted_without_a_warning() -> None:
    report = _profile(FIXTURE_PATH)
    assert report.amount_reviews is not None
    bills = dict(report.amount_reviews)
    assert bills["BILL_AMT1"].negative_count == 2
    assert not any("bill" in code for code in report.warnings)


def test_nonpositive_limit_and_negative_payment_are_warned(tmp_path: Path) -> None:
    path = tmp_path / "limits.csv"
    _write(
        path,
        [
            list(EXPECTED_COLUMN_NAMES),
            _row(LIMIT_BAL="-1", PAY_AMT1="-4", **{"default payment next month": "1"}),
            _row(ID="8", LIMIT_BAL="0"),
        ],
    )
    report = _profile(path)
    assert report.amount_reviews is not None
    amounts = dict(report.amount_reviews)
    assert amounts["LIMIT_BAL"].negative_count == 1
    assert amounts["LIMIT_BAL"].zero_count == 1
    assert amounts["PAY_AMT1"].negative_count == 1
    assert "nonpositive_credit_limit" in report.warnings
    assert "negative_payment_amount" in report.warnings


def test_magnitude_bands_hide_the_planted_amount(tmp_path: Path) -> None:
    path = tmp_path / "magnitude.csv"
    _write(
        path,
        [
            list(EXPECTED_COLUMN_NAMES),
            _row(ID=PLANTED_ID, LIMIT_BAL=PLANTED_AMOUNT),
        ],
    )
    report = _profile(path)
    rendered = render_default_client_report(report)
    assert report.amount_reviews is not None
    limit = dict(report.amount_reviews)["LIMIT_BAL"]
    assert dict(limit.above_threshold_counts)[12] == 1
    assert limit.largest_absolute_magnitude == 12
    assert PLANTED_ID not in rendered
    assert PLANTED_AMOUNT not in rendered
    assert "87654321" not in rendered


def test_row_schema_error_does_not_stop_later_rows(tmp_path: Path) -> None:
    path = tmp_path / "broken-row.csv"
    _write(
        path,
        [
            list(EXPECTED_COLUMN_NAMES),
            _row(LIMIT_BAL="NOT_A_NUMBER", ID=PLANTED_ID),
            _row(ID="8", **{"default payment next month": "1"}),
        ],
    )
    report = _profile(path)
    rendered = render_default_client_report(report)
    assert report.body_scanned is True
    assert report.scan_status == "complete"
    assert report.schema_status == "failed"
    assert dict(report.schema_errors)["LIMIT_BAL:unparseable"] == 1
    assert report.row_count == 2
    assert report.rows_parsed == 1
    assert _counts(report.target_counts) == {"0": 0, "1": 1}
    assert PLANTED_ID not in rendered
    assert "NOT_A_NUMBER" not in rendered


def test_bad_header_makes_quality_counts_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "bad-header.csv"
    _write(path, [list(TRANSACTION_HEADERS), ["1"] * len(TRANSACTION_HEADERS)])
    report = _profile(path)
    rendered = render_default_client_report(report)
    assert report.body_scanned is False
    assert report.scan_status == "interrupted"
    assert report.quality_results_status == "unavailable"
    assert report.schema_status == "failed"
    assert dict(report.schema_errors)["header:mismatch"] == 1
    assert report.target_counts is None
    assert report.amount_reviews is None
    assert report.duplicate_detection_status == "unavailable"
    assert "target_counts:\n  unavailable" in rendered
    assert "\n  0: 0" not in rendered


def test_full_row_duplicates_are_counted_when_detection_is_complete() -> None:
    report = _profile(FIXTURE_PATH)
    assert report.duplicate_detection_status == "complete"
    assert report.duplicate_extra_rows == 1
    assert report.duplicate_distinct_rows == 1
    assert "duplicate_rows" in report.warnings


def test_fingerprint_limit_withholds_both_duplicate_totals() -> None:
    report = _profile(FIXTURE_PATH, duplicate_fingerprint_limit=1)
    rendered = render_default_client_report(report)
    assert report.duplicate_detection_status == "incomplete"
    assert report.duplicate_extra_rows is None
    assert report.duplicate_distinct_rows is None
    assert report.repeated_id_extra_rows is None
    assert report.repeated_id_distinct_rows is None
    assert "duplicate_detection_incomplete" in report.warnings
    assert "duplicate_rows" not in report.warnings
    assert "Duplicate totals are unavailable" in rendered
    assert report.quality_results_status == "complete"
    assert _counts(report.target_counts)["1"] == 1


def test_duplicate_at_fingerprint_cap_stays_complete(tmp_path: Path) -> None:
    path = tmp_path / "copy-at-cap.csv"
    copied = _row()
    _write(path, [list(EXPECTED_COLUMN_NAMES), copied, copied])
    report = _profile(path, duplicate_fingerprint_limit=1)
    assert report.duplicate_detection_status == "complete"
    assert report.duplicate_extra_rows == 1
    assert report.duplicate_distinct_rows == 1
    assert report.repeated_id_extra_rows == 1
    assert report.repeated_id_distinct_rows == 1
    assert "duplicate_rows" in report.warnings
    assert "duplicate_detection_incomplete" not in report.warnings


def test_zero_fingerprint_limit_withholds_duplicate_totals(tmp_path: Path) -> None:
    path = tmp_path / "limit-zero.csv"
    _write(path, [list(EXPECTED_COLUMN_NAMES), _row()])
    report = _profile(path, duplicate_fingerprint_limit=0)
    assert report.row_count == 1
    assert report.rows_parsed == 1
    assert report.quality_results_status == "complete"
    assert report.duplicate_detection_status == "incomplete"
    assert report.duplicate_fingerprint_limit == 0
    assert report.duplicate_extra_rows is None
    assert report.duplicate_distinct_rows is None
    assert report.repeated_id_extra_rows is None
    assert report.repeated_id_distinct_rows is None
    assert "duplicate_detection_incomplete" in report.warnings
    assert "duplicate_rows" not in report.warnings
    assert _counts(report.target_counts) == {"0": 1, "1": 0}


def test_header_only_file_is_a_completed_empty_scan(tmp_path: Path) -> None:
    path = tmp_path / "header-only.csv"
    _write(path, [list(EXPECTED_COLUMN_NAMES)])
    report = _profile(path)
    rendered = render_default_client_report(report)
    assert report.body_scanned is True
    assert report.scan_status == "complete"
    assert report.quality_results_status == "complete"
    assert report.schema_status == "ok"
    assert report.row_count == 0
    assert report.rows_parsed == 0
    assert report.schema_errors == ()
    assert _counts(report.target_counts) == {"0": 0, "1": 0}
    assert report.duplicate_detection_status == "complete"
    assert report.duplicate_extra_rows == 0
    assert report.warnings == ()
    assert "target_counts:\n  0: 0" in rendered
    assert "unavailable" not in rendered


def test_coded_header_csv_is_profiled(tmp_path: Path) -> None:
    path = tmp_path / "coded.csv"
    _write(path, [list(CODED_HEADER), list(EXPECTED_COLUMN_NAMES), _row(ID="8")])
    report = _profile(path)
    assert report.schema_status == "ok"
    assert report.scan_status == "complete"
    assert report.body_scanned is True
    assert report.row_count == 1
    assert report.rows_parsed == 1
    assert report.schema_errors == ()
    assert _counts(report.target_counts) == {"0": 1, "1": 0}


def test_invalid_xls_row_does_not_stop_a_later_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Cell:
        def __init__(self, ctype: int, value: object) -> None:
            self.ctype = ctype
            self.value = value

    class Sheet:
        def __init__(self, rows: list[list[Cell]]) -> None:
            self.nrows = len(rows)
            self.ncols = len(rows[0])
            self._rows = rows

        def cell(self, row_index: int, column_index: int) -> Cell:
            return self._rows[row_index][column_index]

    class Book:
        def __init__(self, sheet: Sheet) -> None:
            self._sheet = sheet
            self.released = False

        def sheet_names(self) -> list[str]:
            return ["Data"]

        def sheet_by_name(self, name: str) -> Sheet:
            return self._sheet

        def release_resources(self) -> None:
            self.released = True

    numbers = [
        8,
        500,
        1,
        2,
        1,
        29,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        10,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        1,
    ]
    bad_row = [Cell(xlrd.XL_CELL_NUMBER, value) for value in numbers]
    bad_row[1] = Cell(xlrd.XL_CELL_TEXT, "NOT_A_NUMBER")
    sheet = Sheet(
        [
            [Cell(xlrd.XL_CELL_TEXT, name) for name in EXPECTED_COLUMN_NAMES],
            bad_row,
            [Cell(xlrd.XL_CELL_NUMBER, value) for value in numbers],
        ]
    )
    book = Book(sheet)

    def open_workbook(filename: Path, **kwargs: object) -> Book:
        assert kwargs["on_demand"] is True
        assert kwargs["formatting_info"] is False
        return book

    monkeypatch.setattr(
        "creditlens_contracts.default_clients_quality.xlrd.open_workbook",
        open_workbook,
    )
    path = tmp_path / "synthetic-rows.xls"
    path.write_bytes(b"placeholder")
    report = _profile(path)
    rendered = render_default_client_report(report)
    assert book.released
    assert report.body_scanned is True
    assert report.scan_status == "complete"
    assert report.schema_status == "failed"
    assert dict(report.schema_errors)["LIMIT_BAL:unparseable"] == 1
    assert report.row_count == 2
    assert report.rows_parsed == 1
    assert _counts(report.target_counts) == {"0": 0, "1": 1}
    assert "NOT_A_NUMBER" not in rendered


def test_disabled_duplicate_detection_does_not_allocate_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("fingerprint set was allocated")

    monkeypatch.setattr(
        "creditlens_contracts.default_clients_quality._new_set",
        fail_if_called,
    )
    report = _profile(FIXTURE_PATH, detect_duplicates=False)
    rendered = render_default_client_report(report)
    assert report.duplicate_detection_status == "disabled"
    assert report.duplicate_fingerprint_limit is None
    assert report.duplicate_extra_rows is None
    assert report.repeated_id_extra_rows is None
    assert "Duplicate detection was not run." in rendered
    assert "duplicate_rows" not in report.warnings


def test_repeated_ids_on_different_rows_are_not_printed(tmp_path: Path) -> None:
    path = tmp_path / "same-id.csv"
    _write(
        path,
        [
            list(EXPECTED_COLUMN_NAMES),
            _row(ID=PLANTED_ID, BILL_AMT1="10"),
            _row(ID=PLANTED_ID, BILL_AMT1="11", **{"default payment next month": "1"}),
        ],
    )
    report = _profile(path)
    rendered = render_default_client_report(report)
    assert report.duplicate_extra_rows == 0
    assert report.repeated_id_extra_rows == 1
    assert report.repeated_id_distinct_rows == 1
    assert "duplicate_client_id" in report.warnings
    assert PLANTED_ID not in rendered


def test_unreadable_workbook_interrupts_the_scan(tmp_path: Path) -> None:
    path = tmp_path / "notes.xls"
    path.write_bytes(b"this is not a workbook")
    report = _profile(path)
    rendered = render_default_client_report(report)
    assert report.scan_status == "interrupted"
    assert report.body_scanned is False
    assert dict(report.schema_errors)["workbook:unreadable"] == 1
    assert report.age_bands is None
    assert "unavailable" in rendered
    assert "this is not a workbook" not in rendered


def test_missing_sheet_is_a_file_level_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Book:
        def sheet_names(self) -> list[str]:
            return ["Notes"]

        def release_resources(self) -> None:
            self.released = True

    book = Book()

    def open_workbook(filename: Path, **kwargs: object) -> Book:
        assert kwargs["on_demand"] is True
        assert kwargs["formatting_info"] is False
        return book

    monkeypatch.setattr(
        "creditlens_contracts.default_clients_quality.xlrd.open_workbook",
        open_workbook,
    )
    path = tmp_path / "sheets.xls"
    path.write_bytes(b"placeholder")
    report = _profile(path)
    assert report.scan_status == "interrupted"
    assert dict(report.schema_errors)["sheet:mismatch"] == 1
    assert report.quality_results_status == "unavailable"
    assert book.released  # type: ignore[attr-defined]


def test_cli_writes_under_outputs_and_can_skip_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    rejected = main(
        ["--input", str(FIXTURE_PATH), "--output", str(tmp_path / "report.txt")]
    )
    assert rejected == 2
    code = main(
        [
            "--input",
            str(FIXTURE_PATH),
            "--output",
            str(tmp_path / "outputs" / "report.txt"),
            "--no-duplicates",
        ]
    )
    assert code == 0
    written = (tmp_path / "outputs" / "report.txt").read_text(encoding="utf-8")
    assert "input_filename: quality_default_clients.csv" in written
    assert "duplicate_detection_status: disabled" in written
    assert str(FIXTURE_PATH.parent.parent) not in written


def test_transaction_command_remains_the_transaction_profiler() -> None:
    from creditlens_contracts.__main__ import main as package_main
    from creditlens_contracts.quality import main as transaction_main

    assert package_main is transaction_main
