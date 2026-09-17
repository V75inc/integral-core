---
name: register_asset
description: Register a new asset through the Asset Register app operation after collecting required fields.
spec: jv
requires-actions: [EmbeddedIntegralAction]
extends: action:integral/embedded_integral_action
allowed-tools: []
---

# Register asset

## When to use

The user wants to add a new asset to the Asset Register catalog.

## Procedure

1. Collect asset tag, title, category, and optional warranty/purchase fields.
2. Invoke the `register_asset` app operation — do not claim success before a result.
3. Return grounded entry references from the operation output.

## Forbidden

- Inventing asset tags or ids.
- Direct substrate writes bypassing the app operation.
