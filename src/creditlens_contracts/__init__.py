"""Transaction schema, labeled-client schema, and read-only quality profiling.

The supervised model, API, and database are not part of this package.
The labeled-client contract lives in ``default_clients`` and is not the
17-column transaction contract exported from this package root.
"""

from creditlens_contracts.default_clients import (
    DefaultClientRecord,
    LabelSchemaError,
    read_default_clients_csv,
    read_default_clients_xls,
)
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
    "DefaultClientRecord",
    "LabelSchemaError",
    "SchemaError",
    "TransactionRecord",
    "profile_transactions",
    "read_default_clients_csv",
    "read_default_clients_xls",
    "read_transaction_csv",
    "render_report",
]
