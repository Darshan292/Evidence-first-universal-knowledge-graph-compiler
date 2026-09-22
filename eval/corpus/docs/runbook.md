# Orderflow Runbook

## Incident: charges hanging

If charges appear to hang, check the gateway deadline.

The payment gateway deadline is **60 seconds**. If you see timeouts before that,
the gateway itself is degraded, not our configuration.

> NOTE: this runbook was last reviewed in 2024 and may lag the current
> configuration.

## Escalation

Page the payments on-call rotation.
