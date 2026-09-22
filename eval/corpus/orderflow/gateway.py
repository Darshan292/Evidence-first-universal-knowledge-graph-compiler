"""Thin client over the external card gateway."""
import time

from orderflow.config import PAYMENT_TIMEOUT_SECONDS, RETRY_LIMIT, BACKOFF_BASE_SECONDS


class GatewayError(Exception):
    """Raised when the gateway rejects or times out."""


class CardGateway:
    """Talks to the card network."""

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.deadline = PAYMENT_TIMEOUT_SECONDS

    def authorize(self, card_token, amount):
        """Authorize an amount, giving up after the configured deadline."""
        return self._call("authorize", card_token, amount)

    def capture(self, auth_id):
        return self._call("capture", auth_id)

    def _call(self, op, *args):
        for attempt in range(RETRY_LIMIT):
            try:
                return self._attempt(op, args)
            except GatewayError:
                time.sleep(BACKOFF_BASE_SECONDS ** attempt)
        raise GatewayError("gateway exhausted retries")

    def _attempt(self, op, args):
        return {"op": op, "args": args, "deadline": self.deadline}
