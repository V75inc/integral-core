# Integrated code candidate requalification — 2026-09-30

## Candidate

- **Source revision:** `746400a690a18856b2e00b1c8dd0242cefefccab` on local branch `codex/c6-current-requalification`.
- **Composition:** starts from integrated checkpoint `60f6e6fa4c22181ba17bba7b7e2d6001678cafc8`, then applies W5.2 declared-query work, W6.2 durability work and its fail-closed fix, and PR #93's backend/frontend dependency remediations. The final commit corrects exact primary-key lookup for durable open-batch records.
- **Candidate status:** local qualification snapshot only. It is not on `main`, is not a frozen release candidate, and has no browser or signed independent-artifact evidence on this exact SHA.

## Qualification results

- `make verify` — **passed** after staging the complete source diff. All 16 substrate guards, pre-commit hooks, formatting, lint/type checks, Core wheel build/import, CI-faithful backend smoke, full frontend suite, and full backend suite passed.
- Frontend suite — **213 files, 1,274 tests passed**.
- Full backend suite — **passed**. The default run skipped its explicitly gated PostgreSQL-only cases, Atlas integration (no `ATLAS_TEST_URI`), and the opt-in resolver benchmark.
- `make test-postgres` — **passed** against PostgreSQL. PostgreSQL-only contracts ran; the Atlas integration and opt-in benchmark remained skipped by their declared setup gates.
- Staging regression tests — cancellation now removes the durable snapshot; failed deletion leaves the in-memory batch available; restart/revision-binding remains functional. The underlying defect was querying a top-level `id` through `Object.find_one`, which maps ordinary query keys beneath `context`; the store now uses `Object.get` for the deterministic primary key. The same correction prevents duplicate durable batch rows during updates.
- The verification-built Core wheel was `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `ae9d3d88825f9f64d5af2ca4b9dd2c34c355cdfd17f24b02c40c3159dc1fdc1e`.

## Evidence boundary

This exact SHA has current repository and PostgreSQL suite evidence. The 2026-09-30 browser smoke, 60f6e6f Core/SDK/App artifact identities, and W5.2 browser fixture belong to their separately named source SHAs and do not transfer here. The candidate still needs the applicable C6 browser/transport matrix and immutable Core/SDK/App artifact identities on one frozen SHA. W0.3b external corpus custody and Product Owner architecture/release review remain open. No release is approved by this record.
