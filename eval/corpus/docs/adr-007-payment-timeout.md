# ADR-007: Payment gateway deadline

**Status:** Accepted
**Date:** 2025-03-11

## Decision

We wait **30 seconds** for the card network before abandoning an authorization.

## Rationale

Load testing showed the card network's 99th percentile response at 18 seconds
under peak traffic. A 30 second ceiling leaves headroom without holding customer
checkout sessions open indefinitely. Beyond roughly 30 seconds, shoppers abandon
the basket anyway, so waiting longer converts nothing and ties up connections.

## Consequences

Slow authorizations fail rather than hang. The retry policy compensates for
transient failures.
