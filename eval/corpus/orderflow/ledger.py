"""Double-entry ledger."""
from orderflow.config import SETTLEMENT_BATCH_SIZE


class Ledger:
    """Append-only record of money movements."""

    def __init__(self):
        self.entries = []

    def record(self, amount, detail):
        self.entries.append((amount, detail))
        return len(self.entries)

    def settle(self):
        """Settle entries in batches."""
        batch = self.entries[:SETTLEMENT_BATCH_SIZE]
        self.entries = self.entries[SETTLEMENT_BATCH_SIZE:]
        return batch
