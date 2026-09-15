# integral-core extract manifest

**Status:** Monorepo prepared (2026-09-15). Public repo initial commit waits on
the open GitHub remote URL.

## Topology (dependency pin)

```
integral-core (public)  ──semver tag──►  integral (private commercial)
                                              │
                                              └── packages/apps/  (INTEGRAL_PACKAGE_PATHS)
```

Commercial **never forks** Core source. Updates = bump the Core pin (git tag /
Docker image), same pattern as `jvspatial` / `jvagent`.

See [CORE_PIN.md](CORE_PIN.md) for the bump runbook.

## Include in public `integral-core`

| Path | Notes |
| --- | --- |
| `backend/app/` | Substrate; `profiles/` = `personal-context` + `agent-scratch` only |
| `frontend/` | Core palette manifests under `src/views/manifests/` — **not** `productManifests/` |
| `docs/product/FOUNDATION_EXTENSION_SAAS.md`, `ROADMAP.md` (foundation sections) | |
| `docs/platform/extension-contract*.md`, `docs/INVARIANTS.md` | |
| `examples/reference-hello-app`, `examples/reference-commercial-hello` | Contract proofs |
| `backend/tests/contract/`, `backend/tests/core_only/` | |
| Core Docker `--target core`, `make verify-core-only` | |
| Root README / LICENSE / CONTRIBUTING | Apache-2.0 default |

## Exclude (stay private commercial)

| Path | Notes |
| --- | --- |
| `packages/apps/**` | All former `community_app` packages + underscore import shims |
| `frontend/src/views/productManifests/` | Domain FE widget registration |
| `scripts/seed_product.py` | Product dogfood seed |
| Private deploy secrets, commercial-only CI | |

## Monorepo layout after unbundle

```
backend/app/profiles/     # CORE seeds only (CI: .ci/core_profiles_only_check.sh)
packages/apps/            # commercial Apps (git history preserved via git mv)
examples/                 # external contract packages
```

Default package path resolution: Core seeds + `packages/apps` when present.
`INTEGRAL_CORE_ONLY=1` still filters the catalog to `core_package`.

## Initial public commit (agent)

1. Filter-copy include paths from this monorepo (fresh tree; NOTICE points at
   V75inc/integral ancestry — avoid full history rewrite).
2. Drop `productManifests/`, `packages/apps/`, product seed.
3. Default Docker target = `core`.
4. Tag `v0.1.0`.
5. Commercial pins that tag (Phase 4).
