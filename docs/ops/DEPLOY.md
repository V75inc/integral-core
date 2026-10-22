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

Use Python 3.11 or later. The frontend toolchain's Node engine requirement applies. Source development normally uses Vite's proxy to port 4000. Configure the appropriate development environment explicitly; `DEBUG` is false by default.

## Packaged distribution

A Core wheel contains the web interface and CLI. Install the exact reviewed wheel, use `integral init` to create a distribution directory, and inspect `integral web --help` for supported serving options. An initialization without `--slug` leaves `integral-apps/` empty. An App package root is configured with `INTEGRAL_PACKAGE_PATHS`.

Do not replace the frozen source lock with an unrestricted extra package index. Runtime dependencies resolve from PyPI, including the built-in native harness. Choose release artifacts from the reviewed release lane rather than inferring a publication from source metadata.

## Production setup

Provision a qualified PostgreSQL deployment and persistent file storage. Keep `DEBUG=false`, `TESTING` unset, and rate limiting active. Start with one worker until the intended shared-state and browser journeys are qualified.

Provide JWT, OAuth, and model-credential encryption keys through a secret manager. Configure public URL, allowed origins, reverse proxy, TLS, CSP, email delivery, provider policy, retention, and a real attachment scanner as required. Core-only boot does not disable the resident or agentive layer.

The durable work runtime fails closed when required transaction/CAS APIs or indexes are unavailable. Mongo is not a qualified production work store; JSON/SQLite are development stores with single-worker limitations. Native chat uses durable WorkItems by default and requires PostgreSQL transaction/CAS support. `INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED=false` opts out to legacy request-bound turns, which do not survive browser disconnects or process restarts.

Review App trust tiers and signature policy before loading packages with Python. Configure host extensions deliberately; do not import commercial App logic into Core.

## Search rollout and recovery

For pgvector retrieval, provision the `vector` extension and a deployment role
allowed to create the passage table and profile indexes. Bake or persist the
configured local ONNX model cache; a chat provider credential does not configure
embeddings. Apply the [retrieval settings](CONFIGURATION.md#indexing-and-retrieval)
and restart. Primary writes queue durable work while bounded reconciliation
covers existing records; the legacy single-vector backfill script is not a
passage migration.

For a representative authorized Track, inspect
`GET /api/tracks/{id}/search-index` in its bound workspace. Verify encoder and
store readiness, pending/failed work and current-source coverage. Coverage over
1000 sources/jobs is explicitly truncated; do not treat it as proof of whole-site
completion. Request `POST /api/tracks/{id}/search-index/reindex` to repair or force
a rebuild, then inspect status and retrieve a saved paraphrase plus an exact
identifier. Check the cited record and test revoked access separately.

A model/profile change starts a separate space and backfill. Old profile rows
are retained and are not a fallback for a different model. Track their storage
cost; no automatic old-profile pruning is promised. Semantic outages fall back
to canonical keyword reads with a retry cooldown. Failed indexing jobs retry;
correct the deployment or source error and inspect coverage again. Keep the
canonical graph and its index intent in backups. Restored or missing derived
passages can be rebuilt from that graph. See the
[indexing guide](../backend/semantic-indexing.md) for lifecycle details and the
[qualification procedure](QUALIFICATION.md#retrieval-qualification) before promotion.

## Backup and restore

Back up the graph database and attachment bytes as one recoverable deployment. Preserve encryption keys securely; database backups alone cannot decrypt records if their keys are lost. Treat session retention, product transcripts, audit records, and commercial usage retention separately.

Restore into an isolated environment first. Verify user access, records, relations, files, App definitions, and any enabled durable work. A volume existing on disk is not evidence that a restore can recover the application.

`scripts/pg_backup.sh` produces a verified logical archive. Use
`scripts/pg_restore.sh ARCHIVE --drill` for an isolated count/identity rehearsal,
or `--into DATABASE` for a separately created copy. Any nonzero `pg_restore`
exit fails the restore immediately, even if counts could match. An explicit
target can contain partial data after an error; successful diagnostics do not
override the failed restore. Drill scratch databases are removed on exit.

The encrypted-state restore contract is an explicit integration gate. Set
`INTEGRAL_TEST_POSTGRES_DSN` to a **disposable PostgreSQL server**: the test
fixtures create/reset their `integral_test` database. With Docker available,
run from `backend/`:

```bash
INTEGRAL_TEST_DB=postgres INTEGRAL_TEST_RESTORE=1 \
  .venv/bin/python -m pytest tests/contract/test_checkpoint_restore_postgres.py
```

The client container must reach that server. Set `INTEGRAL_RESTORE_CLIENT_HOST`
when its host name differs inside Docker, and `PGDUMP_DOCKER_IMAGE` when a
different PostgreSQL client major version is required. This gate backs up
test-only encrypted snapshots, recovery manifests and a synthetic credential,
reads a separate restored copy in a fresh process, rejects foreign scope and
wrong keys, then runs the scratch drill. It does not qualify attachment bytes,
session invalidation, live provider credentials, or automatic continuation of
an interrupted production run.

## Upgrade

Build and identify the candidate, run source and artifact gates, back up, and review migrations and dependency changes. Deploy the exact candidate to a test environment. Check schema transition status and supported recovery before promotion.

For a failed upgrade, inspect the schema and effect state before reverting an image. A schema migration or external effect may make a blind rollback unsafe. Reconcile pending work and use the reviewed restore or compatibility path.

## jvspatial 0.2.2 release gate

Current metadata pins `jvspatial==0.2.2` and `pydantic-ai-harness[skills]==0.36.0`. Use `uv sync --frozen --extra dev --extra test`. Run `make verify`, the clean wheel gate, Core-only checks, and relevant PostgreSQL contracts.

When repairing duplicate UserModelCredential records, query through `GraphContext.find` before creating a unique index. Test with actual duplicates, then verify the repaired index rejects another duplicate. Production OAuth requires `JVSPATIAL_OAUTH_KEY_ENCRYPTION_KEY`. `RATE_LIMIT_DISABLED=1` bypass is restricted to pytest or DEBUG; it does not justify removing the production auth cap.

Continue with [configuration](CONFIGURATION.md), [security](SECURITY.md), [signing](OPERATIONAL_MODEL_SIGNING.md), and [qualification](QUALIFICATION.md).

## Session renewal policy

See [Session policy](SESSION_POLICY.md) for deployment timeout settings, longer
defaults, renewal behavior, and the distinction between the browser idle lock and
server token validity.

### Chat concurrency and model profile ceilings

`INTEGRAL_CHAT_WORKERS` controls the bounded durable chat worker pool (default 4,
range 1–16 per API process). Independent conversations can execute concurrently;
per-thread admission and work leases still serialize a conversation. Account for
replica count, provider rate limits and local-model capacity when setting this.

Settings → AI Models profiles can lower model request, tool-call, turn token and
turn timeout limits. Operator settings remain ceilings: `INTEGRAL_NATIVE_TURN_REQUEST_LIMIT`,
`INTEGRAL_NATIVE_TURN_TOKEN_LIMIT`, `INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS`,
`INTEGRAL_NATIVE_MODEL_REQUEST_TIMEOUT_SECONDS` and
`INTEGRAL_NATIVE_MODEL_IDLE_TIMEOUT_SECONDS`. Unset profile values inherit defaults.
See [AI configuration and latency](../implementation/ai-model-settings-and-latency.md)
for migration, routing, metrics and measurement limits.
