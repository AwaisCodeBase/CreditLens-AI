# Labeled credit-card quality profile

This profiler reads a file that already matches the 25-column contract in `src/creditlens_contracts/default_clients.py`. It counts patterns. It does not change the file, drop rows, or replace unusual codes with more common ones.

It is not a lending model. The codes below are review conventions for this historical Taiwan credit-card dataset. They are not a rule for approving or refusing credit, and they do not describe the separate transaction extract.

Run it with:

```bash
python -m creditlens_contracts.default_clients_quality --input path/to/clients.csv --output outputs/labeled-quality-report.txt
```

`python -m creditlens_contracts` is still the transaction profiler. It does not read this file. The labeled profiler does not read `AI_CREDIT_SCORING_TABLE.csv`.

## Schema failures and quality warnings

A schema failure means a value could not be read as the contracted type. The profiler stores the stable code, such as `target:empty` or `LIMIT_BAL:unparseable`, and continues with the next row. The failed cell is left out of the quality counts. The message text is not used, and the cell value is not stored.

A bad header, a workbook that is not the single `Data` sheet, or a file `xlrd` cannot open stops the scan before data rows are measured. `body_scanned` is false, `scan_status` is `interrupted`, and `quality_results_status` is `unavailable`. Target counts, code counts, age bands, amount bands, and duplicate totals then say `unavailable`. That is not a count of zero.

A completed scan can still have `schema_status: failed` when some rows did not parse. The quality counts then cover only the rows that parsed. Zero in those counts means none of the parsed rows fell in that bin.

A quality warning means the parsed value is worth a look. The row stays in the file. A negative bill is counted and does not raise a warning, because a bill can be a credit balance. A smaller target class is reported as a count and does not raise a warning. `target_class_absent` is used only when every parsed row is the same class.

## What is counted

Education codes `0` through `6` are counted separately. Any other integer is `other`. The paper's dictionary is `1` through `4`, so `0`, `5`, `6`, and `other` raise `undocumented_education_code`. Those values are not rewritten as `4`.

Marriage codes `0` through `3` are counted separately. Any other integer is `other`. The paper's dictionary is `1` through `3`, so `0` and `other` raise `undocumented_marriage_code`.

`SEX` counts `1` and `2`. Every other integer adds to `undocumented` and raises `undocumented_sex_code`. The value is not rewritten.

Each repayment column counts `-2`, `-1`, `0`, and `1` through `9`, plus one `other` total. The paper's scale is `-1` and `1` through `9`. Under this profiler's review convention, `-2` and `0` are recognized ambiguous codes, not unknown codes. They raise `ambiguous_repayment_status` and do not raise `undocumented_repayment_status`. A code outside `{-2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9}` is counted as `other` and raises `undocumented_repayment_status`. The report does not print an unexpected code.

Age bands are negative, zero, under 18, 18 through 100, and over 100. Ages outside 18 through 100 raise `age_outside_review_band`. The report does not print an age.

`LIMIT_BAL`, each bill, and each payment use the same magnitude summary as the transaction profiler: how many values are negative, how many are zero, how many are above `10^6`, `10^9`, and `10^12`, and the order of magnitude of the largest absolute value, written as `10^k`. The exact extreme value is not printed. A negative or zero credit limit raises `nonpositive_credit_limit`. A negative payment raises `negative_payment_amount`.

## Duplicates and memory

Full-row duplicates hash the 25 parsed fields into a 16-byte BLAKE2s digest. Repeated ids are tracked in a second set. Neither set grows past `--duplicate-fingerprint-limit` (100,000 by default). If either set would pass that cap, `duplicate_detection_status` becomes `incomplete`, and both the full-row totals and the repeated-id totals are `unavailable`. A partial tally is not shown as the whole file.

`--no-duplicates` does not allocate those sets. The status is `disabled`.

Ids stay in the running set only. They are not written to the report. CSV rows are read one at a time. `xlrd` still holds an `.xls` workbook in memory; the profiler does not keep a second list of client records.

The report copies the file name, not the full path. Do not rename the file so that its name contains a person identifier.

## How to run it

Tests use invented fixtures and do not open the real workbook:

```bash
pytest
```

A missing file, a file that is not UTF-8, or an output path outside `outputs/` exits with code 2. Schema failures exit with code 1 after the report is written. Warnings alone exit with code 0.
