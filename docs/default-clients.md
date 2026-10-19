# Default of credit card clients contract

This contract reads the UCI Default of Credit Card Clients file. It checks column names, column order, and whether each value can be parsed. It does not train a model, and it does not score the separate transaction extract.

The rules live in `src/creditlens_contracts/default_clients.py`. That module is not `schema.py`. The transaction profiler does not call it.

## Dataset

The public dataset is historical credit-card account data from Taiwan. Each row is one client. The label says whether that client defaulted the next month. This repository's transaction CSV is a different file about different activity. The two files must not be joined, and a model trained here is not evidence about those transactions or about a real lending decision.

The local workbook has one sheet, `Data`. Row 1 of that sheet is a coded header. Row 2 is the real header. The 30,000 client rows begin on row 3. Those positions were checked in a read-only inspection. The tests do not open that workbook. They use invented rows in `tests/fixtures/synthetic_default_clients.csv`.

## Columns

The real header has these 25 names, in this order:

`ID`, `LIMIT_BAL`, `SEX`, `EDUCATION`, `MARRIAGE`, `AGE`, `PAY_0`, `PAY_2`, `PAY_3`, `PAY_4`, `PAY_5`, `PAY_6`, `BILL_AMT1`, `BILL_AMT2`, `BILL_AMT3`, `BILL_AMT4`, `BILL_AMT5`, `BILL_AMT6`, `PAY_AMT1`, `PAY_AMT2`, `PAY_AMT3`, `PAY_AMT4`, `PAY_AMT5`, `PAY_AMT6`, `default payment next month`.

There is no `PAY_1` column. The contract keeps that name as it appears in the file.

`ID`, the six repayment-status columns, `SEX`, `EDUCATION`, `MARRIAGE`, and `AGE` are integers. `LIMIT_BAL`, the six bill amounts, and the six payment amounts are decimals. A negative bill amount or a negative repayment-status code is kept. The parser does not rewrite category codes that fall outside the original paper's dictionary. Those checks belong in a later quality profile.

The target header contains spaces, so the record attribute is `default_payment_next_month`.

## Headers

A CSV or workbook may start with the real 25-column header. That is the header the synthetic fixture uses.

The local workbook starts with a coded row instead: the first cell is empty, the next cells are `X1` through `X23`, and the last cell is `Y`. The readers skip that row only when it matches this sequence exactly and the next row is the real header. Any other first row is a header mismatch. A coded row with no real header after it is a missing header. Reordered, missing, and extra columns are rejected. The parser does not guess a column by a similar name.

## Target

`default payment next month` is the label. `1` means the client defaulted the next month. `0` means the client did not. The parser accepts an integer 0 or 1, and in text it accepts only the exact strings `0` and `1`. It rejects a blank, `2`, `1.5`, `yes`, and any other token. It does not treat a Boolean as a label.

## Readers

`read_default_clients_csv` yields one record at a time. It does not build a list of the file. `read_default_clients_xls` asks `xlrd` to open a legacy `.xls` file in read-only mode, selects the sheet named `Data`, and then yields one record at a time. `xlrd` still holds the workbook in memory; that is a limit of the reader, not a second copy made by this contract. Both functions check the header before parsing a data row. Neither function writes to the source file.

`xlrd` exposes a legacy numeric cell as a Python number. An integer-valued number is stored as an exact integer decimal. Any other number is converted with `Decimal(str(value))`. That string does not guarantee the original decimal representation. This is a parsing limitation. The reader does not round or rewrite the value to hide it.

`xlrd` cannot create a workbook, and this project does not add a writer package. The automated workbook tests therefore use an in-memory sheet for the success path and a temporary non-workbook file for the unreadable-file path. They do not open the real UCI file.

A workbook that is not exactly one `Data` sheet raises `sheet:mismatch`. A file `xlrd` cannot open raises `workbook:unreadable`.

## Error codes

Callers must branch on `LabelSchemaError.code`. Messages do not contain cell values, client identifiers, or amounts.

| Code | When it is raised |
|---|---|
| `header:missing` | The file has no header row, or a coded row is not followed by the real header. |
| `header:mismatch` | The real header is reordered, incomplete, extended, or not the documented names. |
| `row:missing_columns` | A data row has fewer fields than the header. |
| `row:unexpected_columns` | A data row has a field that is not in the contract. |
| `record:fields_mismatch` | The record attributes drifted away from the column list. |
| `ID:empty`, `LIMIT_BAL:empty`, and the same `:empty` form for every other required column | That cell is blank. |
| `ID:unparseable`, `LIMIT_BAL:unparseable`, and the same `:unparseable` form for every other non-target column | That cell is not the required integer or decimal. |
| `target:empty` | The default column is blank. |
| `target:invalid` | The default column is present but is not 0 or 1. |
| `sheet:mismatch` | The workbook is not exactly the `Data` sheet. |
| `workbook:unreadable` | `xlrd` could not open the file. |

`SEX`, `EDUCATION`, `MARRIAGE`, `AGE`, each `PAY_*` column, each `BILL_AMT*` column, and each `PAY_AMT*` column use their own header in the `:empty` and `:unparseable` codes. The target codes stay `target:empty` and `target:invalid`.

## Privacy

The readers do not print rows. A failed cell is reported by its code, not by the text or number that failed. Do not log the exception's cause chain if a lower-level library included a path or a cell there. Tests use invented identifiers and amounts, and they check that those strings do not appear in the contract's own message.

## Limitations

This parser answers whether a file matches the 25-column contract. It does not decide that a row is a good credit risk. It does not fill blanks, drop negative bills, or map education and marriage codes onto the paper's labels. It does not read `AI_CREDIT_SCORING_TABLE.csv`. A model trained on the Taiwan credit-card file would still need a separate decision before anyone used it on the transaction extract or in a lending workflow. No such model is in this repository.
