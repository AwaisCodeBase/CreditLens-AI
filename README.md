# CreditLens

CreditLens is a credit-risk assessment project in its foundation stage. This repository currently defines two column contracts, one for a transaction extract and one for the UCI default-of-credit-card-clients file. Each contract is checked with a small synthetic fixture.

No web application, database, or trained model is included yet.

## Sensitive source file

`AI_CREDIT_SCORING_TABLE.csv` may be kept in this folder for local development. It can contain financial and personal information, so Git is configured to ignore it. Do not commit it, copy it into docs, or sample it into tests.

`default of credit card clients.xls` also contains financial and client data. Keep that workbook local, and leave it excluded from Git.

The tests use `tests/fixtures/synthetic_transactions.csv` and `tests/fixtures/synthetic_default_clients.csv`, which are invented data.

## Schema

The 17-column contract lives in `src/creditlens_contracts/schema.py`. That file documents column order and how each column is parsed:

- `SUBSCRIBERID` is text, so it is not treated as a number.
- `TRANSACTIONDATE` is a timestamp.
- `TRANSFERAMOUNT`, `CHARGEAMOUNT`, and `PREVIOUSBALANCE` are decimals.
- Other profile fields are text. Blank age is accepted at this layer.

Range and data-quality rules are not part of schema parsing. They are reported separately by the profiler in `src/creditlens_contracts/quality.py`. See [docs/data-quality.md](docs/data-quality.md). That pass is read-only: it counts warnings and does not delete or correct rows.

The separate 25-column contract for the UCI Default of Credit Card Clients file lives in `src/creditlens_contracts/default_clients.py`. See [docs/default-clients.md](docs/default-clients.md). It does not read the transaction extract.

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

A supervised credit-risk model needs a labeled outcome. The transaction CSV does not provide one. The UCI workbook has a binary default label, and its parser is documented in [docs/default-clients.md](docs/default-clients.md). No model has been trained in this repository. A model fit on that historical file would not automatically describe the transaction extract or a real lending decision.
