# W5.2 declared-query transport parity

**Source commit:** `3c146077039bd54002eff44050f25506edffc0a7`
**Date:** 2026-09-29
**Result:** deterministic parity contract passed; C6 transport qualification remains partial.

The extracted-App contract test
[`test_declared_asset_query_matches_dashboard_http_resident_and_mcp`](../../../backend/tests/contract/test_asset_register_artifact.py)
builds and extracts the independent Asset Register archive, installs it into a
disposable workspace, and creates three available Assets with distinct tags.
It runs one declared `available_assets` query through Core's query dispatcher,
the authenticated extension HTTP route, the resident tool dispatch path, and
the MCP governed-query dispatch path. It also resolves the dashboard
suggestion preview from the same installed App and fixture.

The mounted-transport test
[`test_mcp_declared_app_query_matches_authenticated_http_query`](../../../backend/tests/test_mcp_server_oauth.py)
installs the checked-in Asset Register example into a disposable workspace,
creates the same three-row query shape, and calls both the authenticated
extension HTTP route and `tools/call integral_governed_query` through Core's
mounted Streamable HTTP MCP endpoint using a bearer token and bound workspace
header. The MCP result's row projections and object references match the query
dispatcher result and the HTTP response.

The assertions prove:

- The dispatcher and HTTP route return identical output rows; resident
  dispatch returns the same output.
- MCP dispatch and the mounted authenticated MCP route return the same three
  Entry projections and object references.
- HTTP and MCP evidence both bind to the same `ws:<workspace-id>` scope and
  expose the same object membership as the query output.
- The dashboard preview reports value and `total_matched` of 3.

Validation on the source commit:

```text
pytest -vv --tb=short tests/contract/test_asset_register_artifact.py tests/test_mcp_server_oauth.py
36 passed, 1 skipped in 44.95s
```

The skipped case requires PostgreSQL. This run used the default local test
store. The mounted MCP request is exercised through an in-process ASGI
transport, not a deployed external network connection. No durable read receipt
on every surface or browser acceptance on this exact commit is claimed. The
previously recorded PostgreSQL browser proof of the visible dashboard value is
separate evidence. Candidate-level C6 parity still needs one frozen
source/artifact set with aligned browser, HTTP, resident, and MCP traces and
receipt semantics.
