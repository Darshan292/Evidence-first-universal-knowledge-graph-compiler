"""HTTP-ish entry points."""
from orderflow.payments import build_processor
from orderflow import reporting


def post_charge(endpoint, card_token, amount):
    processor = build_processor(endpoint)
    return processor.charge(card_token, amount)


def get_report(period):
    return reporting.summarize(period)
