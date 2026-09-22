"""Runtime configuration for the orderflow service."""

# How long we wait for the payment gateway before abandoning a charge.
PAYMENT_TIMEOUT_SECONDS = 30

# Retry policy for transient gateway failures.
RETRY_LIMIT = 3
BACKOFF_BASE_SECONDS = 2

# Ledger batch size for nightly settlement.
SETTLEMENT_BATCH_SIZE = 500

DATABASE_URL = "sqlite:///orderflow.db"
