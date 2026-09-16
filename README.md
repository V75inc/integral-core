# integral-core

**Integral Foundation** — open-core substrate for the Integral knowledge platform.

This repository is the **Core** product: graph persistence (jvspatial), workspace
APIs, Core FE view palette, and the `agent-scratch` seed package. It does **not**
bundle commercial Apps (including `personal-context`) or domain content profiles.

Commercial / private deployments pin this repo by git tag
(https://github.com/V75inc/integral-core) and load Apps via
`INTEGRAL_PACKAGE_PATHS` (see `docs/product/CORE_PIN.md` and
`docs/product/INTEGRAL_CORE_EXTRACT.md`). Releases: `rcN` → TestPyPI, final →
PyPI — see `RELEASING.md`.

## Quick start

```bash
cd backend
uv sync --frozen --extra dev --extra test
INTEGRAL_CORE_ONLY=1 .venv/bin/python -m app.main
```

### Local sandbox (side-by-side with commercial monorepo)

Monorepo typically owns Postgres `:5433`, API `:4000`, SPA `:9006`. The Core
sandbox uses a **separate** Postgres volume/DB and ports:

| Surface | Port / name |
|---------|-------------|
| Postgres | `:5435` → db `integral_core` (`integral-core-sandbox-pg`) |
| API | `:4002` |
| SPA | `:9007` |

```bash
cp .env.sandbox.example .env.sandbox   # set JWT + CREDENTIAL keys
./scripts/sandbox-up.sh                # starts db + API + SPA
# open http://localhost:9007  /  http://localhost:4002/docs
./scripts/sandbox-down.sh              # stop API+SPA
./scripts/sandbox-down.sh --db         # also stop Postgres (volume kept)
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

`v0.1.1rc1` — first TestPyPI cut (Foundation Phase One Core).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
