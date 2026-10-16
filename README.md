# CreditLens

CreditLens is a credit-risk assessment project in its foundation stage. This repository currently defines the column contract for a transaction extract and checks that contract with a small synthetic fixture.

No web application, database, or trained model is included yet.

## Sensitive source file

`AI_CREDIT_SCORING_TABLE.csv` may be kept in this folder for local development. It can contain financial and personal information, so Git is configured to ignore it. Do not commit it, copy it into docs, or sample it into tests.

The tests use `tests/fixtures/synthetic_transactions.csv`, which is invented data.

## Schema

The 17-column contract lives in `src/creditlens_contracts/schema.py`. That file documents column order and how each column is parsed:

- `SUBSCRIBERID` is text, so it is not treated as a number.
- `TRANSACTIONDATE` is a timestamp.
- `TRANSFERAMOUNT`, `CHARGEAMOUNT`, and `PREVIOUSBALANCE` are decimals.
- Other profile fields are text. Blank age is accepted at this layer.

Range and data-quality rules are not enforced yet.

## Setup

Python 3.12 is the target. This was checked with Python 3.12.13.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Tests

```bash
pytest
```

## Modeling limit

A supervised credit-risk model needs a labeled outcome, such as repayment or default. The original CSV does not provide one. Selecting a labeled training dataset is a later decision, and no model has been trained in this repository.
