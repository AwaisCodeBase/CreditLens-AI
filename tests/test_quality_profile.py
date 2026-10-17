"""Quality-profile tests. They use invented fixtures and must not open real datasets."""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from creditlens_contracts.quality import main, profile_transactions, render_report
from creditlens_contracts.schema import EXPECTED_COLUMN_NAMES, SCHEMA_CONTRACT_VERSION

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quality_transactions.csv"
BAD_HEADER_PATH = Path(__file__).parent / "fixtures" / "quality_bad_header.csv"


def _fixed_clock() -> datetime:
    return datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)


SENSITIVE_COLUMNS = (
    "SUBSCRIBERID",
    "JOBINFORMATION",
    "SUBCITY",
    "SECTOR",
    "CITYNAME",
    "STATENAME",
    "COUNTRYNAME",
    "EDUCATIONLEVELNAME",
    "MONTHLYINCOMERANGE",
)


def _profile(
    *,
    detect_duplicates: bool = True,
    duplicate_fingerprint_limit: int = 100_000,
    subscriber_limit: int = 100_000,
    service_allowlist: frozenset[str] | None = None,
):
    return profile_transactions(
        FIXTURE_PATH,
        detect_duplicates=detect_duplicates,
        duplicate_fingerprint_limit=duplicate_fingerprint_limit,
        service_allowlist=service_allowlist,
        clock=_fixed_clock,
        subscriber_limit=subscriber_limit,
    )


def _counts(items: tuple[tuple[str, int], ...]) -> dict[str, int]:
    return dict(items)


def test_metadata_names_the_contract_and_the_file() -> None:
    report = _profile()
    assert report.input_filename == "quality_transactions.csv"
    assert report.profiled_at == datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)
    assert report.row_count == 9
    assert report.rows_parsed == 8
    assert report.schema_contract_version == SCHEMA_CONTRACT_VERSION == "1"
    assert str(FIXTURE_PATH.resolve()) not in render_report(report)


def test_missing_values_are_counted_without_changing_rows() -> None:
    report = _profile()
    blanks = _counts(report.blank_row_counts)
    assert set(blanks) == set(EXPECTED_COLUMN_NAMES)
    assert blanks["AGE"] == 1
    assert blanks["GENDER"] == 1
    assert blanks["EDUCATIONLEVELNAME"] == 6
    assert blanks["JOBINFORMATION"] == 4
    assert blanks["CITYNAME"] == 4
    assert blanks["SUBSCRIBERID"] == 0
    assert blanks["TRANSFERAMOUNT"] == 0
    coverage = _counts(report.subscriber_nonblank_coverage)
    assert coverage["AGE"] == 5
    assert coverage["EDUCATIONLEVELNAME"] == 1
    assert coverage["JOBINFORMATION"] == 2
    assert "sparse_demographic_fields" in report.warnings
    assert "age_missing" in report.warnings


def test_unusual_ages_are_bucketed_and_kept() -> None:
    report = _profile()
    assert _counts(report.age_buckets) == {
        "blank": 1,
        "negative": 1,
        "zero": 1,
        "under_18": 1,
        "age_18_to_100": 2,
        "over_100": 2,
    }
    assert "age_outside_review_band" in report.warnings


def test_extreme_amounts_use_thresholds_and_magnitudes() -> None:
    report = _profile()
    reviews = dict(report.amount_reviews)
    transfer = reviews["TRANSFERAMOUNT"]
    charge = reviews["CHARGEAMOUNT"]
    balance = reviews["PREVIOUSBALANCE"]
    assert transfer.negative_count == 1
    assert transfer.zero_count == 1
    assert transfer.above_threshold_counts == ((6, 2), (9, 0), (12, 0))
    assert transfer.largest_absolute_magnitude == 6
    assert charge.negative_count == 0
    assert charge.zero_count == 7
    assert charge.largest_absolute_magnitude == 0
    assert balance.above_threshold_counts == ((6, 1), (9, 1), (12, 1))
    assert balance.largest_absolute_magnitude == 12
    rendered = render_report(report)
    assert "largest_absolute_magnitude: 10^12" in rendered
    assert "1000000000000.01" not in rendered
    assert "2000000" not in rendered
    assert "extreme_transfer_amount" in report.warnings
    assert "extreme_previous_balance" in report.warnings
    assert "negative_transfer_amount" in report.warnings
    assert "extreme_charge_amount" not in report.warnings


def test_duplicate_rows_are_counted_and_can_be_disabled() -> None:
    enabled = _profile(detect_duplicates=True)
    disabled = _profile(detect_duplicates=False)
    assert enabled.duplicate_detection_enabled is True
    assert enabled.duplicate_detection_status == "complete"
    assert enabled.body_scanned is True
    assert enabled.duplicate_extra_rows == 1
    assert enabled.duplicate_distinct_rows == 1
    assert "duplicate_rows" in enabled.warnings
    assert disabled.duplicate_detection_enabled is False
    assert disabled.duplicate_detection_status == "disabled"
    assert disabled.duplicate_extra_rows is None
    assert disabled.duplicate_distinct_rows is None
    assert "duplicate_rows" not in disabled.warnings
    assert disabled.age_buckets == enabled.age_buckets
    assert disabled.amount_reviews == enabled.amount_reviews
    assert disabled.row_count == enabled.row_count
    assert "not_run" in render_report(disabled)


def test_inconsistent_subscriber_profiles_are_counts_only() -> None:
    report = _profile()
    inconsistent = _counts(report.subscriber_inconsistent_profiles)
    assert inconsistent["GENDER"] == 1
    assert inconsistent["AGE"] == 0
    assert inconsistent["JOBINFORMATION"] == 0
    assert inconsistent["CITYNAME"] == 0
    assert report.subscriber_results_status == "complete"
    assert report.subscribers_stored == 6
    assert report.subscriber_rows_not_covered == 0
    assert _counts(report.subscriber_activity)["1"] == 4
    assert _counts(report.subscriber_activity)["2_to_10"] == 2
    assert "inconsistent_subscriber_profile" in report.warnings


def test_malformed_input_is_a_schema_failure() -> None:
    report = _profile()
    assert report.schema_status == "failed"
    assert report.schema_errors == (("TRANSFERAMOUNT:unparseable", 1),)
    assert "schema_failed" not in report.warnings
    assert sum(count for _, count in report.age_buckets) == report.rows_parsed == 8

    broken = profile_transactions(BAD_HEADER_PATH, clock=_fixed_clock)
    rendered = render_report(broken)
    assert broken.schema_status == "failed"
    assert broken.schema_errors == (("header:mismatch", 1),)
    assert broken.body_scanned is False
    assert broken.row_count == 0
    assert broken.duplicate_detection_status == "unavailable"
    assert broken.duplicate_extra_rows is None
    assert broken.duplicate_distinct_rows is None
    assert broken.subscriber_results_status == "unavailable"
    assert broken.warnings == ()
    assert "The body was not scanned. Duplicate results are unavailable." in rendered
    assert "extra_rows: 0" not in rendered
    assert "extra_rows: unavailable" in rendered


def test_report_omits_identifiers_jobs_locations_and_raw_rows() -> None:
    rendered = render_report(_profile())
    with FIXTURE_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames is not None
        for row in reader:
            raw_line = ",".join(row[column] for column in reader.fieldnames)
            assert raw_line not in rendered
            for column in (*SENSITIVE_COLUMNS, "SERVICENAME"):
                value = (row[column] or "").strip()
                if value:
                    assert value not in rendered
    assert "NOT_A_NUMBER" not in rendered


def test_source_fixture_is_unchanged() -> None:
    before = FIXTURE_PATH.read_bytes()
    before_stat = FIXTURE_PATH.stat()
    _profile()
    after = FIXTURE_PATH.read_bytes()
    after_stat = FIXTURE_PATH.stat()
    assert after == before
    assert hashlib.sha256(after).hexdigest() == hashlib.sha256(before).hexdigest()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns


def test_subscriber_limit_keeps_row_checks() -> None:
    limited = _profile(subscriber_limit=1)
    full = _profile()
    assert limited.subscriber_results_status == "partial"
    assert limited.subscribers_stored == 1
    assert limited.subscriber_rows_not_covered == 6
    assert limited.subscriber_results_status != "complete"
    rendered = render_report(limited)
    assert "subscriber_results_status: partial" in rendered
    assert "subscriber_results_scope: stored_subscribers_only" in rendered
    assert "cover stored subscribers only" in rendered
    assert "subscriber_check_limit_reached" in limited.warnings
    assert limited.row_count == full.row_count
    assert limited.age_buckets == full.age_buckets
    assert limited.amount_reviews == full.amount_reviews
    assert _counts(limited.subscriber_inconsistent_profiles)["GENDER"] == 1


def test_cli_writes_only_under_outputs_and_can_skip_duplicates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    rejected = main(
        ["--input", str(FIXTURE_PATH), "--output", str(tmp_path / "report.txt")]
    )
    assert rejected == 2
    assert not (tmp_path / "report.txt").exists()

    code = main(
        [
            "--input",
            str(FIXTURE_PATH),
            "--output",
            str(tmp_path / "outputs" / "report.txt"),
            "--no-duplicates",
        ]
    )
    assert code == 1
    written = (tmp_path / "outputs" / "report.txt").read_text(encoding="utf-8")
    assert "duplicate_detection_enabled: false" in written
    assert "input_filename: quality_transactions.csv" in written
    assert FIXTURE_PATH.read_bytes().startswith(b"SUBSCRIBERID,")


def test_duplicate_fingerprint_limit_withholds_partial_counts() -> None:
    limited = _profile(duplicate_fingerprint_limit=1)
    complete = _profile()
    rendered = render_report(limited)
    assert limited.duplicate_detection_status == "incomplete"
    assert limited.duplicate_extra_rows is None
    assert limited.duplicate_distinct_rows is None
    assert limited.body_scanned is True
    assert "duplicate_detection_incomplete" in limited.warnings
    assert "duplicate_rows" not in limited.warnings
    assert "Partial duplicate counts are not reported." in rendered
    assert "extra_rows: unavailable" in rendered
    assert "extra_rows: 0" not in rendered
    assert "extra_rows: 1" not in rendered
    assert limited.age_buckets == complete.age_buckets
    assert limited.amount_reviews == complete.amount_reviews
    assert limited.row_count == complete.row_count


def test_service_names_stay_hidden_unless_allowlisted() -> None:
    hidden = _profile()
    rendered = render_report(hidden)
    assert hidden.service_allowlisted_counts == ()
    assert hidden.service_non_allowlisted_rows == 8
    assert hidden.service_distinct_complete is True
    assert hidden.service_distinct_count == 4
    assert "SyntheticTransfer" not in rendered
    assert "SyntheticAirtime" not in rendered

    shown = _profile(service_allowlist=frozenset({"SyntheticTransfer"}))
    shown_text = render_report(shown)
    assert ("SyntheticTransfer", 5) in shown.service_allowlisted_counts
    assert "SyntheticTransfer: 5" in shown_text
    assert "SyntheticAirtime" not in shown_text
    assert "SyntheticDeposit" not in shown_text
    assert "SyntheticBill" not in shown_text
