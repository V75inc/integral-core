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
- `make verify-independent-artifacts` — **passed**: Core wheel isolated import, clean-install ASGI import, SDK wheel import, and signed external Asset Register handler load. The first run's temporary outputs were not retained; the retained build and load proof below supplies artifact identities for this candidate.
- Retained exact-checkout artifacts were built and the retained wheels/archive were installed or loaded in a clean environment. Core wheel `integral_core-0.1.1rc11-py3-none-any.whl`: SHA-256 `cdd5db5e5d0a0e0799aa4e9743eff76a17518ea591b77e860c097627f7620f71`; SDK wheel `integral_sdk-0.2.0-py3-none-any.whl`: `30d56972563ac0e3cba87dc22e56b2af7fea4a08edea9551bbf0a391c8b4f6ef`; signed App archive `asset-register-1.0.0.tar.gz`: `4040244680f93d36d48e4ef5dbd95b2ca0d9c65ccff53bfcf56ea9d69b820f05`; verification public-key file: `85999e0ab815a2a02cb198a0c17ff77e29994fee251e4001e602818796171742`. The generated private key was not retained.
- Staging regression tests — cancellation now removes the durable snapshot; failed deletion leaves the in-memory batch available; restart/revision-binding remains functional. The underlying defect was querying a top-level `id` through `Object.find_one`, which maps ordinary query keys beneath `context`; the store now uses `Object.get` for the deterministic primary key. The same correction prevents duplicate durable batch rows during updates.
- The verification-built Core wheel was `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `ae9d3d88825f9f64d5af2ca4b9dd2c34c355cdfd17f24b02c40c3159dc1fdc1e`.

## Evidence boundary

This exact SHA has repository, PostgreSQL, and independent Core/SDK/external-App loading evidence, with Core/SDK/App archive identities retained above. The 2026-09-30 browser smoke and local container image identities belong to `60f6e6f` and do not transfer here. The candidate still needs its C6 browser/transport matrix and exact API/web image identities on one frozen SHA. W0.3b external corpus custody and Product Owner architecture/release review remain open. No release is approved by this record.
