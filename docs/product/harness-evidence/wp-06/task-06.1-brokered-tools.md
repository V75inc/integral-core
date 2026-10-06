# WP-06.1 — Brokered Pydantic tools evidence

`build_brokered_tools` adapts each current Core catalogue entry into a Pydantic AI `Tool` while preserving the catalogue's name, description, and JSON Schema. Each tool's closed-over capability identity is fixed at construction. Its invocation uses server-provided `HarnessExecutionScope` IDs and calls Integral's `invoke_declared_capability`; no model tool argument becomes tenant, principal, run, or session identity. It returns the existing model-safe capability result/receipt. The Core broker remains responsible for live declaration, workspace, policy, and effect checks.

Offline TestModel evidence executes a generated Pydantic tool through the adapter, and two tools built in one loop dispatch their own names. This confirms the Pydantic lifecycle reaches the Core broker adapter. It does not qualify actual jvspatial capability receipts, user permissions, staging session compatibility, Prompt Sheet suspension, app-specific dynamic tool discovery, or external model tool calling.

Validation on Python 3.14.3:

- `.venv/bin/pytest tests/native_harness/wp_06/test_broker_tools.py -q` — 1 passed.
- Black, isort, and flake8 passed over the Harness module and test.
- No provider call was made.
