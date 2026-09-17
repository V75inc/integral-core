---
name: prepare_asset_checkout
description: Prepare a checkout proposal for an available asset and custodian via staging flow.
spec: jv
requires-actions: [EmbeddedIntegralAction]
extends: action:integral/embedded_integral_action
allowed-tools: []
---

# Prepare asset checkout

## When to use

The user wants to check out an available asset to a custodian.

## Procedure

1. Confirm the asset is available (`list_available_assets` or known grounded id).
2. Confirm the custodian entry id.
3. Stage/prepare `check_out_asset` with explicit user approval before execute.
4. Report conflict codes (`state_conflict`) without retrying blindly.

## Forbidden

- Executing checkout before approval.
- Claiming checkout succeeded before operation output.
