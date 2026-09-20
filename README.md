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

## Prerequisites

| Path | Need |
|------|------|
| Docker (recommended) | [Docker Desktop](https://docs.docker.com/get-docker/) |
| From source | Docker (Postgres), [uv](https://docs.astral.sh/uv/), Python 3.10+, Node 18+ |
| PyPI | Docker (Postgres), Python 3.10+ |

Postgres is required. The Docker path starts it for you.

## Quick start — Docker

From a clone, one command:

```bash
docker compose up --build
```

Then open:

| Surface | URL |
|---------|-----|
| SPA | http://localhost:9006 |
| API docs | http://localhost:4000/docs |
| Postgres | `localhost:5433` / db `integral` |

No `.env` is required for a first run. Compose injects a local
`JVSPATIAL_JWT_SECRET_KEY` (overrides a placeholder in `.env` so a copied
example file cannot fail boot). To add provider keys or a bootstrap admin:

```bash
cp .env.docker.example .env   # then edit
docker compose up --build
```

Stop with `Ctrl-C`, or `docker compose down` (add `-v` to wipe the Postgres volume).

If `:9006` / `:4000` / `:5433` are already taken (commercial Integral monorepo),
use the [sandbox](#local-sandbox-side-by-side-with-commercial-monorepo) instead.

## PyPI

`integral-core` publishes as a Python package. Current cut is a pre-release on
TestPyPI (`v0.1.1rc1`); `jvagent` RCs also live on TestPyPI, so pip needs both
indexes:

```bash
# 1. Postgres
docker run -d --name integral-pg -p 5433:5432 \
  -e POSTGRES_USER=integral -e POSTGRES_PASSWORD=integral \
  -e POSTGRES_DB=integral pgvector/pgvector:pg16

# 2. Package
pip install "integral-core==0.1.1rc1" \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/

# 3. Secrets + DSN, then run
export JVSPATIAL_JWT_SECRET_KEY="$(openssl rand -hex 32)"
export JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@localhost:5433/integral
export INTEGRAL_CORE_ONLY=1
python -m app.main
```

API: http://localhost:4000/docs

Once a final release is on PyPI (`RELEASING.md`):

```bash
pip install integral-core
```

`jvagent` still needs TestPyPI until its `0.1.8` final is published — keep
`--extra-index-url https://test.pypi.org/simple/` until then.

## From source

```bash
# 1. Env (fills JWT + credential keys; placeholders in the example are not valid)
./scripts/bootstrap_env.sh .env .env.example

# 2. Postgres
docker compose up -d db

# 3. API
cd backend
uv sync --frozen --extra dev --extra test
INTEGRAL_CORE_ONLY=1 .venv/bin/python -m app.main
```

The API reads **repo-root** `.env`. `JVSPATIAL_JWT_SECRET_KEY` must be at least
32 characters and must not be the example placeholder. Generate one with
`openssl rand -hex 32`, or let `bootstrap_env.sh` do it.

Optional SPA (proxies `/api` to `:4000`):

```bash
cd frontend
npm install
npm run dev    # http://localhost:9006
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
./scripts/sandbox-up.sh     # writes .env.sandbox if missing, starts db + API + SPA
# open http://localhost:9007  /  http://localhost:4002/docs
./scripts/sandbox-down.sh   # stop API+SPA
./scripts/sandbox-down.sh --db   # also stop Postgres (volume kept)
```

The script waits for `http://127.0.0.1:4002/health` and **exits non-zero** if
the API never becomes reachable (check `.sandbox/logs/api.log`).

Core image only (no compose):

```bash
docker build --target core -f backend/Dockerfile .
```

## Verify

```bash
make verify-core-only
make verify-contract
```

`verify-core-only` uses `rg` (ripgrep) when present and falls back to `grep`.
Optional: `brew install ripgrep`.

## Version

`v0.1.1rc1` — first TestPyPI cut (Foundation Phase One Core).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
