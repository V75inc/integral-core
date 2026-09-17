---
name: review_warranties
description: Summarize assets with warranties expiring within the configured horizon.
spec: jv
requires-actions: [EmbeddedIntegralAction]
extends: action:integral/embedded_integral_action
allowed-tools: []
---

# Review warranties

## When to use

Scheduled or on-demand warranty review (default horizon 30 days).

## Procedure

1. Invoke `review_warranties` (optionally with `horizon_days`).
2. Summarize expiring assets with grounded entry ids and warranty_end dates.
3. Optionally surface one authorized in-app notice per schedule window — no outbound email.

## Forbidden

- Inventing warranty dates or asset ids.
- Duplicate notifications for the same schedule window without deduplication policy.
