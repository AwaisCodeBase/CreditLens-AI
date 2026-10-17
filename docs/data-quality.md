# Transaction data-quality profile

The quality profiler reads a transaction CSV once and writes an aggregate report. It does not change the source file, delete rows, or fill in replacement values.

Column names and parsing rules come from `src/creditlens_contracts/schema.py`. This document describes the checks that sit on top of that contract.

## Schema failures and quality warnings

A schema failure means a value could not be read as the contracted type. Examples are a header in the wrong order, an empty subscriber id, or an amount that is not a number. Each failure is stored as a stable code, such as `header:mismatch` or `TRANSFERAMOUNT:unparseable`. The profiler does not classify failures by reading the human-readable message. Failed rows are left in the file. Cells that did not parse are left out of the numeric tallies, because there is no honest number to count.

A bad header stops the scan. `body_scanned` is false, and duplicate and subscriber results are `unavailable`. That is not a count of zero. A bad cell in a valid file is counted, and the rest of the file is still profiled. Schema status is separate from quality warnings.

A quality warning means the value parsed, and a person should look at the pattern. Examples are a blank education field, an age below zero, or a balance above a review threshold. The program counts these and continues. It does not decide that the row is wrong, and it does not correct it.

## What the report contains

Every report records:

- the input filename, not the full path
- the UTC time the profile ran
- the number of data rows seen, and how many of those parsed
- the schema contract version from `schema.py`

It also records counts: blanks, age bands, amount thresholds, service totals, currency codes, rows per day, and subscriber-level inconsistency counts. Service names are not printed unless a caller passes an explicit allowlist. The default allowlist is empty, so the report shows how many rows used a non-allowlisted service and how many distinct services were seen, not the names.

It does not record subscriber ids, raw rows, job titles, education or income text, or location text. Do not rename the input file so that its name contains a person identifier. The filename is copied into the report.

Financial columns do not include the exact largest value. For each of transfer amount, charge amount, and previous balance, the report gives:

- how many values are negative
- how many are zero
- how many are above `10^6`, `10^9`, and `10^12`
- the order of magnitude of the largest absolute value, written as `10^k`

`10^12` means the largest absolute value is at least one trillion and below ten trillion. It is not the value itself. These bands are review tools. They are not a rule for dropping customers.

Age bands are the same kind of tool. Blank age is missing data. Negative, zero, below 18, and above 100 are review bands. Ages from 18 through 100 are counted separately. None of these bands removes a row.

Subscriber checks compare non-blank profile values for the same subscriber. A change increments an inconsistency count. A change can be a real update, so the report does not call it a broken row.

`subscriber_results_status` is `complete` only when every parsed row belongs to a stored subscriber. After the cap, the status is `partial`. `subscribers_stored` is the number of people kept. `subscriber_rows_not_covered` is the number of parsed rows left outside that table. Activity and consistency then apply to stored subscribers only, and the report says so. Those figures are not the full population.

If education, income, job, or location text is blank on at least half of the parsed rows, the report adds `sparse_demographic_fields`.

## Duplicate detection and memory

Duplicate detection is optional and has a hard fingerprint limit. When it is on, the profiler hashes each raw row into a 16-byte digest and stores the digests in a Python set, up to 100,000 distinct fingerprints by default. A second set remembers digests that occur more than once. If the scan finishes under the limit, `duplicate_detection_status` is `complete`, and the report says how many extra copies exist and how many distinct rows have copies. It does not print those rows.

If another distinct fingerprint would exceed the limit, fingerprinting stops. The status becomes `incomplete`. Extra-row and distinct-row totals are `unavailable`. A partial tally is not shown as if it were the whole file. Other checks continue. Pass `--duplicate-fingerprint-limit` to change the cap.

A Python set uses much more memory than 16 bytes per item, because it also stores hash-table slots and pointers. Around one million distinct rows, expect on the order of 100 MB. The default cap is about one tenth of that. That cost is only the duplicate sets.

Turn the check off without stopping the other checks:

```bash
python -m creditlens_contracts --input path/to/extract.csv --no-duplicates
```

In Python, call `profile_transactions(path, detect_duplicates=False)`.

If the file has more than 100,000 distinct subscribers, subscriber consistency stops accepting new subscribers. Row-level counts still finish. `subscriber_results_status` becomes `partial`, and the report adds `subscriber_check_limit_reached`.

Currency codes are listed until 50 names, and dates until 400. Service names are not listed unless allowlisted. Job titles and place names are never listed.

## How to run it

Automated tests use the synthetic fixtures under `tests/fixtures/` and do not read a real extract:

```bash
pytest
```

To profile a local file, write the report under `outputs/`. That directory is gitignored. The command reads the file and does not modify it:

```bash
python -m creditlens_contracts --input path/to/extract.csv --output outputs/transaction-quality-report.txt
```

The process exits with code 1 when the schema status is failed, even if the report was written. Warnings alone leave the exit code at 0. A missing file, a file that is not UTF-8, or an output path outside `outputs/` exits with code 2.

This profiler understands the 17-column transaction contract only. It does not read the UCI spreadsheet.
