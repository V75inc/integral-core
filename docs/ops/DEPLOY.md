# Run and deploy Integral

Choose a source checkout, a locally built wheel, or a container image deliberately. Record its revision and artifact identity. The repository's package version does not prove publication or production qualification.

## Single-command local installation

`integral up` manages a private PostgreSQL database, Core API and packaged UI on loopback. `integral status`, `logs`, `stop`, `backup`, `restore` and `upgrade` manage its lifecycle. See the [local installation guide](LOCAL_INSTALLATION.md) for source preview, persistent storage, recovery and client contract. This launcher is for a personal local installation; hosted deployment follows the production setup below.

## Local container stack

The root Compose file starts a Core-only API, web UI, PostgreSQL, and persistent file storage. It is a local/demo configuration: database credentials and published ports must be changed before wider exposure.

From the repository root:

```bash
./scripts/bootstrap_env.sh .env .env.example
docker compose up --build -d
docker compose ps
```

The bootstrap creates the missing environment file and replaces recognized secret placeholders. Keep generated keys private and stable for the deployment. Review non-secret model, origin, and storage settings separately. Compose requires an operator-provided JWT signing key and OAuth encryption key; it supplies no JWT fallback.

Default host ports are web 9006, API 4000, and PostgreSQL 5433. Use loopback bindings or a reviewed override for local-only access. The file has a fixed database container name, so use a separate override when operating multiple installations on one host.

```bash
curl --fail http://localhost:4000/health
```

A healthy API is only the first check. Sign up or sign in, create a record, reload it, inspect a relation or file, and exercise the provider configuration you intend to use. See [qualification](QUALIFICATION.md).

## Source development

```bash
cd backend
uv sync --frozen --extra dev --extra test
.venv/bin/python -m app.main
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Use Python 3.10 or later. The frontend toolchain's Node engine requirement applies. Source development normally uses Vite's proxy to port 4000. Configure the appropriate development environment explicitly; `DEBUG` is false by default.

## Packaged distribution

A Core wheel contains the web interface and CLI. Install the exact reviewed wheel, use `integral init` to create a distribution directory, and inspect `integral web --help` for supported serving options. An initialization without `--slug` leaves `integral-apps/` empty. An App package root is configured with `INTEGRAL_PACKAGE_PATHS`.

Do not replace the frozen source lock with an unrestricted extra package index. The lock resolves jvspatial from PyPI and the pinned jvagent release candidate from its explicit TestPyPI source. Choose release artifacts from the reviewed release lane rather than inferring a publication from source metadata.

## Production setup

Provision a qualified PostgreSQL deployment and persistent file storage. Keep `DEBUG=false`, `TESTING` unset, and rate limiting active. Start with one worker until the intended shared-state and browser journeys are qualified.

Provide JWT, OAuth, and model-credential encryption keys through a secret manager. Configure public URL, allowed origins, reverse proxy, TLS, CSP, email delivery, provider policy, retention, and a real attachment scanner as required. Core-only boot does not disable the resident or agentive layer.

The durable work runtime fails closed when required transaction/CAS APIs or indexes are unavailable. Mongo is not a qualified production work store; JSON/SQLite are development stores with single-worker limitations. Native durable chat is a separate feature flag and is disabled by default.

Review App trust tiers and signature policy before loading packages with Python. Configure host extensions deliberately; do not import commercial App logic into Core.

## Backup and restore

Back up the graph database and attachment bytes as one recoverable deployment. Preserve encryption keys securely; database backups alone cannot decrypt records if their keys are lost. Treat session retention, product transcripts, audit records, and commercial usage retention separately.

Restore into an isolated environment first. Verify user access, records, relations, files, App definitions, and any enabled durable work. A volume existing on disk is not evidence that a restore can recover the application.

## Upgrade

Build and identify the candidate, run source and artifact gates, back up, and review migrations and dependency changes. Deploy the exact candidate to a test environment. Check schema transition status and supported recovery before promotion.

For a failed upgrade, inspect the schema and effect state before reverting an image. A schema migration or external effect may make a blind rollback unsafe. Reconcile pending work and use the reviewed restore or compatibility path.

## jvspatial 0.1.1 release gate

Current metadata pins `jvspatial==0.1.1` and `jvagent==0.1.8rc20`. Use `uv sync --frozen --extra dev --extra test`. Run `make verify`, the clean wheel gate, Core-only checks, and relevant PostgreSQL contracts.

When repairing duplicate UserModelCredential records, query through `GraphContext.find` before creating a unique index. Test with actual duplicates, then verify the repaired index rejects another duplicate. Production OAuth requires `JVSPATIAL_OAUTH_KEY_ENCRYPTION_KEY`. `RATE_LIMIT_DISABLED=1` bypass is restricted to pytest or DEBUG; it does not justify removing the production auth cap.

Continue with [configuration](CONFIGURATION.md), [security](SECURITY.md), [signing](OPERATIONAL_MODEL_SIGNING.md), and [qualification](QUALIFICATION.md).
