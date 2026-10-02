---
name: register_asset
description: Register a new asset through the Asset Register app operation after collecting required fields.
spec: jv
requires-actions: [EmbeddedIntegralAction]
extends: action:integral/embedded_integral_action
allowed-tools: [integral_invoke_app_operation]
---

# Register asset

## When to use

The user wants to add a new asset to the Asset Register catalog.

## Procedure

1. Collect asset tag, title, category, and optional warranty/purchase fields.
2. Call `integral_invoke_app_operation` with the installed App ID supplied by the host, `operation_key="register_asset"`, and `input` containing the collected fields. Do not use a generic entry-write tool for this operation.
3. Return grounded entry references from the successful operation receipt. If the operation fails, report the error and do not claim registration.

## Forbidden

- Inventing asset tags or ids.
- Direct substrate writes bypassing the app operation.
