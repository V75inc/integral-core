---
name: hello_world
description: Greets the workspace and confirms the Reference Hello App is installed and ready for contract tests.
spec: jv
requires-actions: [EmbeddedIntegralAction]
extends: action:integral/embedded_integral_action
allowed-tools: []
---

# Hello World

## When to use

Use when verifying that the Reference Hello App skill overlay is active in a workspace after an external-package install.

## When not to use

Do not use for domain work — this skill exists only to prove the F0 extension contract.

## Grounding

Confirm the App slug `reference-hello-app` is installed and `lifecycle_state` is `active`.

## Procedure

1. Greet the user.
2. State that the Reference Hello App is installed.
3. Stop.

## Staging

No writes. Read-only confirmation.

## Forbidden

- Creating entries or calling unregistered tools.
- Claiming other Apps are installed without checking.

## Example

User: "Is the reference app working?"
Agent: "Yes — Reference Hello is installed in this workspace."
