# Orderflow Architecture

Orderflow accepts card charges, talks to an external card network, and records
every money movement in an append-only ledger.

## Components

- **api** — entry points. Delegates to the payment layer.
- **payments** — orchestration. Owns `PaymentProcessor`.
- **gateway** — the only module that talks to the card network.
- **ledger** — append-only record of money movements.
- **reporting** — period summaries. Has its own `Processor` class, which is
  unrelated to `PaymentProcessor` despite the similar name.

## Request path

A charge enters through `post_charge`, which builds a processor, authorizes
through the gateway, captures, and records to the ledger.

## Timeouts

The gateway deadline is 30 seconds. Every call inherits it.
