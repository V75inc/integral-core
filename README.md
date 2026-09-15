# integral-core

**Integral Foundation** — open-core substrate for the Integral knowledge platform.

This repository is the **Core** product: graph persistence (jvspatial), workspace
APIs, Core FE view palette, and seed packages (`personal-context`,
`agent-scratch`). It does **not** bundle commercial Apps or content profiles.

Commercial / private deployments pin this repo by git tag and load Apps via
`INTEGRAL_PACKAGE_PATHS` (see `docs/product/CORE_PIN.md` and
`docs/product/INTEGRAL_CORE_EXTRACT.md`).

## Quick start

```bash
cd backend
uv sync --frozen --extra dev --extra test
INTEGRAL_CORE_ONLY=1 .venv/bin/python -m app.main
```

Core Docker image:

```bash
docker build --target core -f backend/Dockerfile .
```

## Verify

```bash
make verify-core-only
make verify-contract
```

## Version

`v0.1.0` — Foundation Phase One complete (Core/App separation, entitlement
kill-switch, App export).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
