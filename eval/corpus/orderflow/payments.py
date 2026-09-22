"""Payment orchestration."""
from orderflow.gateway import CardGateway, GatewayError
from orderflow.ledger import Ledger


class PaymentProcessor:
    """Coordinates authorization, capture and ledger writes."""

    def __init__(self, endpoint, ledger):
        self.gateway = CardGateway(endpoint)
        self.ledger = ledger

    def charge(self, card_token, amount):
        """Charge a card and record the result."""
        auth = self.gateway.authorize(card_token, amount)
        captured = self.gateway.capture(auth)
        self.ledger.record(amount, captured)
        return captured

    def refund(self, auth_id, amount):
        self.ledger.record(-amount, {"refund": auth_id})
        return True


def build_processor(endpoint):
    """Factory used by the API layer."""
    return PaymentProcessor(endpoint, Ledger())
