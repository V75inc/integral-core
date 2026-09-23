# Integral Core

**Integral Core is the open-source operational substrate for people and AI to work from the same knowledge.**

It gives an individual, team, or organization one queryable graph for its
operational knowledge; one access model across that graph; a configurable
schema and view layer; and an always-on resident harness that works through
the same policy gates as its users. It is the foundation for applications
rather than a prebuilt vertical product.

Core contains the reusable platform:

- **Knowledge graph and access policy** — workspaces, Apps, tracks, entries,
  relationships, collaboration, sharing, audit, and permission resolution.
- **Conformable operational models** — Operational Models declare entry types,
  fields, tags, views, relations, and lifecycle rules, then materialize them
  into a workspace.
- **Human work surfaces** — a React workspace with feed, table, board,
  calendar, gallery, and composable views over the same records an agent uses.
- **Resident harness and MCP perimeter** — one active, pluggable resident
  harness per deployment, faceted by principal. It stages proposed writes for
  approval, uses the published tool catalogue, and exposes the same governed
  surface to external agents through MCP.
- **A public App contract** — independently built packages can provide
  domain-specific profiles, tools, operations, skills, schedules, and
  declarative views without importing Core internals.

Core deliberately does **not** contain commercial or domain-specific Apps.
Those are modular extensions loaded through the published contract and
`INTEGRAL_PACKAGE_PATHS`.

For the product thesis and architecture, start with
[the concept](docs/product/CONCEPT.md),
[the resident-harness specification](docs/product/RESIDENT_HARNESS.md), and
[the architecture](docs/product/ARCHITECTURE.md).

## Choose a path

| Goal | Start here |
| --- | --- |
| Run a self-contained Core locally | [Docker quick start](#docker-quick-start) |
| Install a published Core and a blank distro | [Install a released Core](#install-a-released-core) |
| Work on Core from source | [Source development](#source-development) |
| Build a reusable App | [App developer quick start](docs/developer/quickstart.md) |
| Understand the extension boundary | [Extension Contract v1](docs/platform/extension-contract-v1.md) |
| Deploy or operate Core | [Deployment guide](docs/ops/DEPLOY.md) |
| Verify a change before contributing | [Verification](#verification) |

## Prerequisites

| Path | Requirements |
| --- | --- |
| Docker quick start | Docker Desktop or Docker Engine with Compose |
| Released Core (`integral init`, `integral web`) | Python 3.12 and Postgres (this repo's Compose database is enough) |
| Source development | Docker, Python 3.10+, [uv](https://docs.astral.sh/uv/), and Node 18+ |
| App development | A released Core or the source-development requirements, plus the App package’s own tools |

Postgres with pgvector is the supported default datastore. The supplied Compose
stack starts it for you.

## Docker quick start

From a fresh clone, create a local environment file first. The bootstrap
script copies the example when needed and replaces placeholder signing and
credential secrets with fresh local values.

```bash
./scripts/bootstrap_env.sh .env .env.example
docker compose up --build
```

Open these surfaces once the stack is healthy:

| Surface | Address |
| --- | --- |
| Integral workspace | [http://localhost:9006](http://localhost:9006) |
| API reference | [http://localhost:4000/docs](http://localhost:4000/docs) |
| Postgres | `localhost:5433`, database `integral` |

Create an account in the workspace to begin. A personal workspace is
provisioned for the account automatically.

The default Compose image runs **Core-only mode**
(`INTEGRAL_CORE_ONLY=1`). It is suitable for evaluating the substrate,
creating workspaces and models, and using the built-in agentive layer. Load an
external App package only when you intentionally configure an extension path,
as described in the [App developer quick start](docs/developer/quickstart.md).

To stop the local stack:

```bash
docker compose down
```

To discard all local data and start again, remove the volumes:

```bash
docker compose down -v
```

That second command is destructive. It deletes the local Postgres and runtime
volumes.

## Install a released Core

For an evaluation or deployment that starts from a published artifact rather
than this repository, install the Core package into an isolated environment:

Use Python 3.12. Pre-releases are on TestPyPI. Download only the Core wheel
and the matching `jvagent` wheel, then install those files with PyPI as the
only index. A general TestPyPI extra index makes pip select a broken
`fastapi` sdist.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip download \
  --index-url https://test.pypi.org/simple \
  --no-deps \
  --dest ./wheels \
  'integral-core==0.1.1rc5' 'jvagent==0.1.8rc15'
.venv/bin/pip install \
  --index-url https://pypi.org/simple \
  ./wheels/integral_core-*.whl ./wheels/jvagent-*.whl
```

Do not add TestPyPI as a general extra index. That index has published a
broken `fastapi` sdist, and pip will prefer it over the real package.
Download only the two pre-release wheels, then resolve every other
dependency from PyPI. `0.1.1rc5` is the cut that includes `integral web`
and a blank `integral init`. Until that publish, `0.1.1rc4` installs
`integral init` without the UI command, and that init writes a starter App.
`jvagent` stays a version pin (`0.1.8rc15`) because a direct wheel URL is
rejected at upload.

Generate a blank distro. This writes `.env` (JWT secret filled in, Postgres
defaults for host port 5433), `.gitignore`, a README, and an empty
`integral-apps/`. A released Core contains only the generic substrate. Add
Apps through `INTEGRAL_PACKAGE_PATHS`; do not copy an App into the installed
package.

```bash
.venv/bin/integral init ./my-integral
```

Pass `--slug` and `--name` to also write one App. The directory name is
`package.slug`.

```bash
.venv/bin/integral init ./my-integral --slug studio-equipment --name "Studio Equipment Desk"
```

A custom distro is a parent directory of sibling App directories. Core loads
`*/operational-model.yaml` under each path in that variable (one level only).
The directory name must equal `package.slug`. Set `INTEGRAL_CORE_ONLY=0` so
those Apps stay in the library. The published wheel does not include
`app/packages/`, so a pip install has no seed Apps until this path is set.
A source checkout still loads `backend/app/packages/` as well. The
[App developer quick start](docs/developer/quickstart.md) shows the layout.

A process started from the distro directory loads that `.env`. Values already
set in the process environment still win. `integral init` fills the JWT
secret and `INTEGRAL_CREDENTIAL_ENC_KEY`. Settings → AI Models can save a
key only when that encryption key is set. `INTEGRAL_AGENT_KEY_MODE=hybrid`
uses a key saved in Settings when one exists, and otherwise
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`.
Postgres is the database Core expects. `POSTGRES_HOST` defaults to the
Compose hostname `db`, so a process on the host must set `localhost` (and
the published port, `5433` for this repo's Compose database). If
`POSTGRES_PASSWORD` is set, Core builds `JVSPATIAL_POSTGRES_DSN` from the
`POSTGRES_*` variables and ignores a DSN you also wrote.

```text
.venv/                         # the environment that installed integral-core
wheels/
my-integral/                   # integral init ./my-integral
├── .env
├── .gitignore
├── README.md
└── integral-apps/             # empty until an App is added
    └── .gitkeep
```

An App is a child of `integral-apps/` whose directory name equals
`package.slug`:

```text
my-integral/integral-apps/
├── studio-equipment/
│   └── operational-model.yaml
└── client-delivery/
    └── operational-model.yaml
```

```bash
# my-integral/.env
JVSPATIAL_JWT_SECRET_KEY=   # openssl rand -hex 32  (at least 32 characters)
JVSPATIAL_DB_TYPE=postgres
POSTGRES_USER=integral
POSTGRES_PASSWORD=integral
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=integral
INTEGRAL_PACKAGE_PATHS=/absolute/path/to/my-integral/integral-apps
INTEGRAL_CORE_ONLY=0
DEBUG=true
INTEGRAL_CREDENTIAL_ENC_KEY=   # openssl rand -base64 32; integral init fills this
INTEGRAL_AGENT_KEY_MODE=hybrid
# Optional. The API boots and serves workspaces without a model key.
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
# OPENROUTER_API_KEY=
```

`DEBUG=true` is the local switch. Leave it unset on a public host, and set
`OAUTH_ISSUER_URL` and `FRONTEND_ORIGIN` to the public `https://` origins.
The defaults `http://localhost:4000` and `http://localhost:9006` are accepted
only because they are loopback.

Postgres must already be running. From `my-integral/`, start the API with
the venv created above. The process loads `.env` because it was started in
that directory. Values already set in the process environment still win, so
source the file first when the shell should adopt every value in it.

```bash
cd my-integral
../.venv/bin/python -m app.main
```

That listens on port 4000. Set `JVSPATIAL_PORT` in `.env` when that port is
taken, then point `integral web` at the same origin. In a second terminal,
from the directory that contains `.venv` (the parent of `my-integral`):

```bash
.venv/bin/integral web ./my-integral
```

That path is the distro directory. `JVSPATIAL_PORT` in its `.env` selects
the API. `--api http://127.0.0.1:4010` overrides it. With no path, the
current directory is used and the API defaults to port 4000. The
workspace is at http://127.0.0.1:9006. The browser talks only to that port.
`/api` and `/ws` are proxied to the API, including WebSocket upgrade.
`FRONTEND_ORIGIN` can stay `http://localhost:9006`.

A Core checkout still uses `npm run dev` in `frontend/` when you want hot
reload. Compose `web` remains the deploy path. `integral web` is the UI for
a pip-installed Core.

### Local configuration

`.env` is local-only and must never be committed. The bootstrap command
creates valid values for:

- `JVSPATIAL_JWT_SECRET_KEY`, used to sign sessions and API tokens.
- `INTEGRAL_CREDENTIAL_ENC_KEY`, used when encrypted user credentials are
  enabled.

Set a model-provider key such as `OPENAI_API_KEY` only when you want the
resident harness to make model-backed turns. The platform can still start and
serve its normal workspace and API surfaces without a provider key. See
[model credentials and BYOK](docs/backend/model-credentials-byok.md) for the
provider and workspace-key modes.

For an optional bootstrap administrator or a console-email configuration, edit
the generated `.env` using the documented variables in
[.env.example](.env.example). The Compose example
[.env.docker.example](.env.docker.example) is a convenience starting point
for a local Docker-only configuration, not a production secret source.

## Source development

Use two terminals after preparing the environment and database.

```bash
# Terminal 1: prepare dependencies and start the API
./scripts/bootstrap_env.sh .env .env.example
docker compose up -d db

cd backend
uv sync --frozen --extra dev --extra test
INTEGRAL_CORE_ONLY=1 .venv/bin/python -m app.main
```

```bash
# Terminal 2: start the React workspace
cd frontend
npm install
npm run dev
```

The frontend is available at [http://localhost:9006](http://localhost:9006)
and proxies API requests to the backend on port 4000.

For a parallel Core sandbox on ports 9007, 4002, and 5435, use:

```bash
./scripts/sandbox-up.sh
# Open http://localhost:9007
./scripts/sandbox-down.sh
```

## Install and develop an App

Apps are independent packages. They declare their schema and capabilities in a
manifest and access Core only through the public `integral_sdk` and injected
contexts. They must not import `app.models` or `app.services`.

To run the included external reference Apps while developing Core, set the
package path **before** starting the API:

```bash
export INTEGRAL_PACKAGE_PATHS="$PWD/examples"
export INTEGRAL_CORE_ONLY=0
cd backend
.venv/bin/python -m app.main
```

The repository includes:

- `examples/reference-hello-app/` — a small contract example.
- `examples/asset-register/` — a fuller independent-App proof covering
  package installation, typed operations, MCP dispatch, resident dispatch,
  lifecycle, and durable mutations.

Build the portable Asset Register package with:

```bash
make build-asset-register
```

Continue with the [App developer quick start](docs/developer/quickstart.md)
for package layout, signing, API installation, operation invocation, custom
views, and standalone SDK use. The
[Extension Contract v1](docs/platform/extension-contract-v1.md) is the stable
boundary that App authors target.

## Verification

`make verify` is the normal local quality gate. It runs repository guards,
pinned formatters, frontend type checks, frontend and backend tests, and the
CI-faithful backend smoke lane.

```bash
make verify
```

Useful focused commands:

```bash
make verify-ci                    # PR backend job reproduction
make verify-core-only             # Core with no domain packages
make verify-contract              # external App contract tests
make test-frontend                # React/Vitest suite
make test-postgres                # backend suite against local Compose Postgres
make verify-independent-artifacts # isolated Core, SDK, and App artifact proof
```

Start the database with `docker compose up -d db` before
`make test-postgres`. The independent-artifact proof resolves packages in
temporary environments and may need network access.

## Architecture at a glance

```text
Humans and external agents
          │
          ├── React workspace / REST API
          └── MCP
                  │
        Resident harness + staging + skills
                  │
     one policy engine and public tool boundary
                  │
Workspace → App → Track → Entry graph
                  │
      Operational Models and view palette
                  │
           PostgreSQL + pgvector
```

Every human, resident, and external-agent operation is intended to resolve
against the same workspace scope and permission rules. External agents connect
through MCP; Integral does not provide a separate agent-to-agent fabric. Read
[ADR-003](docs/backend/adr/003-singular-resident-harness.md) for the
resident-harness decision and [BYOA](docs/product/BYOA.md) for the
external-agent surface.

## Documentation

The [documentation index](docs/README.md) groups the maintained material by
product, platform, backend, and operations concerns. The most useful references
for a contributor are:

- [AGENTS.md](AGENTS.md) — repository conventions, graph invariants, and the
  required pre-commit checks.
- [Contributing](CONTRIBUTING.md) — contribution workflow.
- [Operational Models](docs/operational-models/README.md) — schema, view, and operational-rule model.
- [App bundles v1](docs/backend/app-bundles-v1.md) — package manifest and
  lifecycle reference.
- [Core finish status](docs/product/CORE_FINISH_STATUS.md) — implemented
  evidence, remaining proof, and the ordered program to the finished state.
- [Releasing](RELEASING.md) — package release procedure.

## License

[Apache License 2.0](LICENSE).
