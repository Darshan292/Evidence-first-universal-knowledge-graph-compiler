# Orderflow API

## POST /charge

Calls `post_charge(endpoint, card_token, amount)`.

Returns the captured authorization. Raises `GatewayError` when the card network
rejects the charge or the deadline elapses.

## GET /report

Calls `get_report(period)`. Returns a period summary from the reporting module.
