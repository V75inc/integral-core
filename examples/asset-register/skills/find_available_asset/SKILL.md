---
name: find_available_asset
description: Find an available asset using list/filter operations with grounded references only.
spec: jv
requires-actions: [EmbeddedIntegralAction]
extends: action:integral/embedded_integral_action
allowed-tools: []
---

# Find available asset

## When to use

The user needs an asset that is currently available for checkout.

## Procedure

1. Call `list_available_assets` with relevant filters (category, location).
2. Present matching assets with entry ids and tags from the operation output.
3. Stop if none are available — do not invent alternatives.

## Forbidden

- Claiming availability without operation output.
- Using title-only identity instead of asset tag or entry id.
