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

Use Python 3.11.5 or later. The frontend toolchain's Node engine requirement applies. Source development normally uses Vite's proxy to port 4000. Configure the appropriate development environment explicitly; `DEBUG` is false by default.

## Packaged distribution

A Core wheel contains the web interface and CLI. Install the exact reviewed wheel, use `integral init` to create a distribution directory, and inspect `integral web --help` for supported serving options. An initialization without `--slug` leaves `integral-apps/` empty. An App package root is configured with `INTEGRAL_PACKAGE_PATHS`.

Do not replace the frozen source lock with an unrestricted extra package index. Runtime dependencies resolve from PyPI, including the built-in native harness. Choose release artifacts from the reviewed release lane rather than inferring a publication from source metadata.

## Production setup

Provision a qualified PostgreSQL deployment and persistent file storage. Keep `DEBUG=false`, `TESTING` unset, and rate limiting active. Start with one worker until the intended shared-state and browser journeys are qualified.

Session-mode graph connections default to PostgreSQL custom prepared plans via
the standard DSN startup `options` setting. This avoids the repeated-read latency
cliff observed on the 100K fixture, at the cost of planning each execution.
Qualify CPU and latency on the intended workload. Use
`INTEGRAL_POSTGRES_PLAN_CACHE_MODE=auto` for PostgreSQL's adaptive choice.
Explicit DSN `options` or `plan_cache_mode` remain authoritative; transaction
poolers receive no new session option. See the backend's governed-read
performance qualification instructions for the isolated scale gate.

The optional exact-count projection covers one configured Track per PostgreSQL
schema. Use a Core build with projection support in its jvspatial dependency.
Set `ENTRY_COUNT_PROJECTION_TRACK_ID` to the target Track ID. In a maintenance
window, run the following inside the configured Core environment. Backfill
holds a write fence on the source tables until publication commits.

```bash
python - <<'PY'
import asyncio
import app.main
from app.config import settings
from app.services.entry_access_projection import configure_entry_count_projection

async def main():
    track_id = settings.ENTRY_COUNT_PROJECTION_TRACK_ID.strip()
    if not track_id or not await configure_entry_count_projection(track_id, rebuild=True):
        raise SystemExit("Configure a Track and a PostgreSQL adapter with projection support")

asyncio.run(main())
PY
```

Workers attach on startup. Core still evaluates live access before and after
counting. Other Tracks, unsupported filters, and invalidated projections use
the native count path. To roll back, call
`await get_default_context().database.invalidate_graph_binding_projection()`
from the configured Core environment. This unpublishes the projection across
workers and stops its maintenance. Remove the environment setting on the next
restart.

Set `ENTRY_SHARED_CREATE_ENABLED=true` for a controlled PostgreSQL rollout of
shared authorization admission for ordinary Entry creates on standalone Tracks.
The create transaction locks the current Track, EntryType, and attached schema
rows. It retries with exclusive admission if the current Track has an App
parent, needs schema materialization, has custom fields, or has save hooks.
Leave the setting unset for the default exclusive create path.

Provide JWT, OAuth, and model-credential encryption keys through a secret manager. Configure public URL, allowed origins, reverse proxy, TLS, CSP, email delivery, provider policy, retention, and a real attachment scanner as required. Core-only boot does not disable the resident or agentive layer.

Mailbox verification and password recovery use the platform's `EMAIL_PROVIDER`,
not an individual workspace's email credentials. Configure `resend` with
`RESEND_API_KEY` or `sendgrid` with `SENDGRID_API_KEY`, plus the verified sender
in `EMAIL_FROM`. Console mail is an explicit `DEBUG=true` preview only; a
production console or unknown provider reports delivery failure rather than
claiming mail was sent or logging a verification code. Confirm provider
acceptance and actual mailbox arrival separately during deployment smoke.
Authenticated verification resend has a 60-second cooldown. A failed resend
retains a previous unexpired code and reports a safe delivery error; it does
not return a success notice merely because a code already exists.

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

A model/profile change starts a separate build while queries pin the serving
profile. Activation requires verified source coverage and committed-intent
catch-up. Continuing calls to a previous provider requires explicit consent;
without it, the existing authorized projection can still serve keyword matches.
Use Settings → AI Models → Embeddings to inspect serving/building progress and
cancel a pending change. Cancellation rechecks the current owner, revision and
provider authority; it is not qualified rollback to an arbitrary old snapshot.

Old profile rows are retained and are not vectors for a different model. Track
their storage cost; no automatic old-profile pruning is promised. Semantic
outages retain the independent keyword path where available, with a retry
cooldown. Failed indexing jobs retry;
correct the deployment or source error and inspect coverage again. Keep the
canonical graph and its index intent in backups. Restored or missing derived
passages can be rebuilt from that graph. See the
[indexing guide](../backend/semantic-indexing.md) for lifecycle details and the
[qualification procedure](QUALIFICATION.md#retrieval-qualification) before promotion.

### Upgrading an existing search deployment

Back up the canonical graph, attachment bytes and required encryption keys before
changing the pinned artifact. Preserve existing model selections and identifiers;
do not delete data or reseed a workspace to refresh a derived index. Verify the
installed Core version in Settings → About after upgrading.

Existing attachment-free fingerprints remain compatible. For attachment-enabled
Tracks, inspect the current `search_index.attachments` selection and coverage.
Use a permitted attachment's metadata panel → Reprocess when existing document
text or image observations need regeneration, then verify a file-specific search
and open its cited Entry. A Track reindex rebuilds the selected projection; it
does not substitute for extracting missing file content. Enabling image analysis
does not itself process every existing file or establish complete coverage.

Automatic image analysis requires PostgreSQL passage storage, transactional work
and a configured vision-capable model. Review Settings → AI Models → Images and
documents before processing a populated workspace: analysis can send image
content to its configured provider. Confirm the deployment and workspace policy,
provider budget and [attachment limits](../backend/attachment-agent-contract.md).
No OCR is used; image-only document pages and audio/video content remain outside
the indexed-content coverage. Validate restore and resource/relevance targets
separately before production promotion.

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
test-only encrypted snapshots, recovery manifests, a synthetic credential,
and a marked mandate reservation. A fresh process reads the restored copy,
rejects foreign scope and wrong keys, and settles the restored hold once when
a synthetic definitive receipt arrives. The source hold remains reserved.
The same gate copies an isolated local attachment store alongside the graph
backup. The restored conversation can read the generated file with the same
digest and media type. A foreign workspace principal is refused, and removing
the file from the restored store leaves the source intact and causes restored
review to fail closed. The scratch drill verifies graph counts and record
identities. Object-store backup, consistency under concurrent writes,
session invalidation, live provider credentials, and automatic continuation
of interrupted production runs require separate qualification.

## Upgrade

Build and identify the candidate, run source and artifact gates, back up, and review migrations and dependency changes. Deploy the exact candidate to a test environment. Check schema transition status and supported recovery before promotion.

For a failed upgrade, inspect the schema and effect state before reverting an image. A schema migration or external effect may make a blind rollback unsafe. Reconcile pending work and use the reviewed restore or compatibility path.

## jvspatial compatibility release gate

Current metadata pins `jvspatial==0.2.9` and `pydantic-ai-harness[skills]==0.59.0`. Use `uv sync --frozen --extra dev --extra test`. Run `make verify`, the clean wheel gate, Core-only checks, and relevant PostgreSQL contracts.

jvspatial 0.2.4 keeps shallow node deletion within the selected node and its incident edges. Qualify App deletion with nested comments and linked content through both the manual dialog and chat approval. Verify atomic graph cleanup and execution receipts, retained shared resources, rollback on failure, and refusal of malformed discussion graphs.

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
See [AI model configuration](../backend/model-credentials-byok.md)
for migration, routing, metrics and measurement limits.

### Optional native light/heavy model roles

Single-model execution remains the default. Workspace owners can opt into Automatic
routing in **Settings → AI Models → Configure routing**, selecting an owned Light
profile and the default Main/heavy profile. Providers and reasoning settings remain
independent; both models share the effective turn limits and existing approval policy.

For deployment-managed models, set `INTEGRAL_NATIVE_ROUTING_MODE=light_heavy` and
`INTEGRAL_NATIVE_LIGHT_MODEL=provider/model`; `INTEGRAL_NATIVE_MODEL` remains Main/heavy.
Optional `INTEGRAL_NATIVE_LIGHT_MODEL_SETTINGS` and
`INTEGRAL_NATIVE_HEAVY_MODEL_SETTINGS` are typed JSON objects using the same bounded
settings contract as profiles. Reasoning controls must be supported by that model.
Update `INTEGRAL_NATIVE_CREDENTIAL_REF` when rotating operator credentials. Strict BYOK
never falls back to deployment models. See the [routing contract](../backend/model-credentials-byok.md#single-or-automatic-routing)
for admission, recovery, measurement limitations and rollback to Single model.

The AI Models page shows deployment roles and their validated tuning as read-only
server-managed settings. ENV keys are not copied into personal Providers. Under
`hybrid`, a usable owner BYOK profile takes precedence; `byo_strict` requires it.

### Entry-read mandate canary

Leave Entry-read mandate execution disabled until the canary workspace and reviewed model route are ready. Set `INTEGRAL_MANDATE_ENTRY_EXECUTION_ENABLED=true` and set `INTEGRAL_MANDATE_ENTRY_WORKSPACE_ALLOWLIST` to a JSON array of exact workspace IDs. Apply the same configuration on all API replicas. An empty array admits no work.

During an upgrade from a release that only recognizes the deployment switch,
keep that switch off on every API replica until all replicas support the
workspace allowlist. Turn it off before rolling back to such a release.
Older replicas can otherwise admit work in an excluded workspace. Keep
receipt recovery running while the replicas change.

Check the following on the release candidate before expanding the allowlist.

1. Open an owned native conversation in the canary workspace. Use a disposable Entry to propose, approve, and run a mandate. Verify the review pins the Entry revision and the result appears in the same private conversation.
2. In an excluded workspace, verify the structured proposal form is absent and direct proposal and Run requests are refused. Existing reviews remain readable by their owners.
3. Exercise Stop on queued work and during execution. Verify that a stop accepted before publication prevents a private result. Restart an isolated worker with an outstanding dispatch intent and verify that recovery does not repeat the request.
4. Verify accounting separately from work status. An undispatched terminal reservation can be released. A marked request with unknown provider cost retains its reservation until definitive evidence arrives.

To stop new admissions, remove the workspace from the allowlist or turn off the deployment switch across all API replicas. An admitted mandate may still run. Use the review's Stop control for specific queued or running work, and leave recovery enabled so it can finish cancellation and reconcile receipts. Keep ordinary chat available and preserve the work and accounting records during rollback.

See the [work mandate contract](../backend/work-mandate-contract.md) for the review and accounting boundaries.

### Workspace email providers

Native workspace SMTP/Resend/SendGrid setup, platform environment variables,
public signed webhooks, queue behavior and the explicit legacy migration are
specified in [workspace email](../platform/workspace-email.md). Configure a real
platform provider for account recovery; console delivery is DEBUG-only. Run the
migration before upgrading existing Business Email Log configuration surfaces.
