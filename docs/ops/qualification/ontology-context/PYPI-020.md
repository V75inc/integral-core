# JV Spatial 0.2.0 PyPI qualification — 11 October 2026

Integral now pins `jvspatial==0.2.0` in its public dependency metadata.
Removed the development Git source override; `uv.lock` resolves the PyPI wheel
with SHA-256 `f819e962dae5a2c5642f6673dfa79374ef6579ae07fbee71f1c4a277869bfde7`.
A frozen `uv sync --frozen --extra dev --extra test` installed version 0.2.0
with no Git/direct-URL provenance. Other resolved dependency versions and the
Integral Core version are unchanged.

This supersedes the missing published dependency boundary in the historical
[dev qualification](DEV-UPDATE.md). No service or skill changes were needed
for compatibility with the published package.

## Verification

- Full `make verify`: **passed**, including staged guards, pre-commit, pinned
  formatting, lint, types, artifact proof, CI smoke, frontend and backend suites.
  Full backend: **5,014 passed, 286 skipped**; optional/infrastructure lanes
  remain explicitly unqualified unless covered by the separate PostgreSQL probes.

- CI-faithful backend smoke: **687 passed, 7 skipped**.
- Frontend: **1,583 passed across 279 files**.

- `make verify-clean-install`: a newly built Core wheel resolved its public
  production dependencies into a fresh virtual environment and imported the
  ASGI application outside the checkout.
- `make verify-core-only`: passed.
- PostgreSQL graph-context/database adoption: **43 passed, zero skipped**.
- PostgreSQL feed access-control regressions: **3 passed, zero skipped**.
- Real authenticated browser checks: **20/20 passed** on the existing fictional
  coastal-pilot dataset, including all eight authorized nodes and seven edges,
  quantities, deadlines, field/relation definitions, explicit missing evidence,
  private and foreign root rejection, denied-bridge exclusion, and feed totals.
  The complete recorded capture was 45,923 bytes.

[Browser receipt](pypi-020-browser-receipt.json). Runtime restarted with the
published package at `http://127.0.0.1:64341/agent` (API port 64339).
The first PostgreSQL probe was interrupted by restarting this same isolated
runtime; the stable-server rerun above passed without skips.

## Ordinary-language resident check

A fresh question asked whether the 2 November coastal pilot could launch if
Northstar missed truck confirmation, or should switch to Seabreeze. DeepSeek
V4.1 Flash Cloud discovered and called `integral_get_graph_context` through the
normal insights workflow. The answer preserved Northstar's unknown capacity,
Seabreeze's unreserved two-vehicle offer, outstanding inspection and insurance,
independent route validation, completed depot approval, and pending go/no-go.

[Answer](pypi-020-query.txt), [tool trace](pypi-020-query-trace.txt),
[screenshot](pypi-020-query.png). The trace recorded six model calls, seven tools,
18.0 seconds and 106,418 tokens; provider cost was unavailable. This is one
manually assessed synthetic response, not a statistical accuracy measurement.

## Boundaries

The dependency is now published and the public-wheel installation gate passes.
This qualification does not deploy production or certify production load,
physical database scan costs, atomic snapshots, or model accuracy across a
population of queries. Captures remain best-effort multi-read evidence and
current authorization is reapplied. All browser data are synthetic.
