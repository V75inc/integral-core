# W5.2 declared-query transport parity

**Source commit:** `8d2a97ae3e98c8d899bcaa3c6f8d2f90e0f920ef`  
**Date:** 2026-09-29  
**Result:** deterministic parity contract passed; C6 transport qualification remains partial.

The contract test
[`test_declared_asset_query_matches_dashboard_http_resident_and_mcp`](../../../backend/tests/contract/test_asset_register_artifact.py)
builds and extracts the independent Asset Register archive, installs it into a
disposable workspace, and creates three available Assets with distinct tags.
It runs one declared `available_assets` query through Core's query dispatcher,
the authenticated extension HTTP route, the resident tool dispatch path, and
the MCP governed-query dispatch path. It also resolves the dashboard
suggestion preview from the same installed App and fixture.

The assertions prove:

- The dispatcher and HTTP route return identical output rows; resident
  dispatch returns the same output.
- The MCP query returns the same three Entry projections and object references.
- HTTP and MCP evidence both bind to the same `ws:<workspace-id>` scope and
  expose the same object membership as the query output.
- The dashboard preview reports value and `total_matched` of 3.

Validation on the source commit:

```text
pytest -vv --tb=short tests/contract/test_asset_register_artifact.py
14 passed, 1 skipped in 35.24s

pytest -vv --tb=short tests/contract/test_asset_register_artifact.py::test_declared_asset_query_matches_dashboard_http_resident_and_mcp
1 passed in 6.29s
```

The skipped case requires PostgreSQL. This run used the default local test
store. The test exercises the in-process dispatch paths used by the resident
and MCP adapters; it does not claim an external MCP network session, a durable
read receipt on every surface, or browser acceptance on this exact commit.
The previously recorded PostgreSQL browser proof of the visible dashboard
value is separate evidence. Candidate-level C6 parity still needs a single
frozen source/artifact set with aligned UI, HTTP, resident, and MCP traces.
