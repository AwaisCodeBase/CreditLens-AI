"""Transaction-file schema and read-only quality profiling for CreditLens.

The supervised model, API, and database are not part of this package.
"""

from creditlens_contracts.quality import profile_transactions, render_report
from creditlens_contracts.schema import (
    COLUMNS,
    EXPECTED_COLUMN_NAMES,
    SCHEMA_CONTRACT_VERSION,
    SchemaError,
    TransactionRecord,
    read_transaction_csv,
)

__all__ = [
    "COLUMNS",
    "EXPECTED_COLUMN_NAMES",
    "SCHEMA_CONTRACT_VERSION",
    "SchemaError",
    "TransactionRecord",
    "profile_transactions",
    "read_transaction_csv",
    "render_report",
]
