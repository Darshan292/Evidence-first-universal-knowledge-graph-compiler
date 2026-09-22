"""Reporting. NOTE: defines its own Processor -- deliberate name collision."""
from orderflow.ledger import Ledger


class Processor:
    """Report processor. Unrelated to payments.PaymentProcessor."""

    def __init__(self):
        self.ledger = Ledger()

    def run(self, period):
        return {"period": period, "count": len(self.ledger.entries)}


def summarize(period):
    p = Processor()
    return p.run(period)
