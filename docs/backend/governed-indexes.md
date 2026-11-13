# Governed PostgreSQL storage

Core's PostgreSQL serving mode is `managed`. The selected jvspatial artifact must
support this capability. Unsupported artifacts and conflicting configurations
fail explicitly; the dependency pin is not changed by this implementation.
`constraints` remains an explicit transitional configuration for an installation
that has not yet completed its reviewed migration. It is not the managed boundary.

## Ownership and invariants

The adapter supplies `IndexSpec` and SQL compilation. Core owns catalog approval,
physical schema lifecycle, provisioning and readiness. Business owns domain query
proposals and its separately privileged host catalog. Core has no Business import.
The graph, workspace, permission, revision, mutation-receipt and event-outbox
contracts in [INVARIANTS](../INVARIANTS.md) remain unchanged. No records or audit
history are deleted by index provisioning.

`app/services/storage_indexes/catalog.py` inventories Object, Node and Edge
annotations, framework auth/logging, raw durable queues, event delivery, exact
keyset expressions and dedicated lexical/vector tables. `approved.json` freezes
physical fingerprints. New optional declarations stay proposals; a new required
unique declaration fails compilation until reviewed. Required uniqueness retains
its exact existing scope and partial predicate. Every generated sort expression
is an explicit catalog member, rather than a serving-time companion build.
Identical physical requirements share a fingerprint; ownership/query references
are metadata. Definition changes require a new plan. Names are bounded, deterministic
fingerprints and never depend on record count or workspace identity.

In managed mode adapter initialization opens connections and checks relations;
it cannot create tables, repair indexes, add vector columns, enable RLS or install
a binding projection. Model unique declarations verify durable catalog receipts.
Core startup verifies PostgreSQL's valid/ready state and stored definition hashes,
then caches integrity verification. A background task repeats checks every minute;
saves do not inspect pg_catalog. Missing integrity or an owning/DDL-capable runtime
role fails readiness. Missing optional access paths are reported as degraded.
Core explicitly selects `JVSPATIAL_POSTGRES_INDEX_CATALOG_TABLE=integral_index_catalog`
for its graph and framework-created log adapters. The foundation's neutral default
is `jvspatial_index_catalog`; Core rejects conflicting catalog configuration rather
than reading a relation its provisioner does not manage.
Fresh provisioning selects the reviewed graph-only profile if pgvector is absent;
later extension availability never triggers a build. Search relation/index absence
raises an unavailable capability; bounded exact
search admission and statement timeouts remain intact. No volume threshold builds
an ANN index. A profile ANN definition must be explicitly included in a plan.

The API and worker use DML-only login roles. The migration role owns tables and
is absent from serving environments. Revoking ordinary permissions from a table
owner does not remove its inherent DDL authority; see [PostgreSQL privileges](https://www.postgresql.org/docs/16/ddl-priv.html).
Do not substitute a superuser DSN for a runtime DSN.

## Operator commands

Run with the selected release/candidate artifact and identical trusted host
providers on the planning, applying, API and worker processes. Example budgets
below are illustrative and must be selected for the target; there are no automatic
retirements or capacity-triggered deletion policies.

```bash
# Runtime read-only credentials: planning executes no DDL.
integral indexes plan --output /secure/index-plan.json \
  --max-indexes 600 --max-index-bytes 4000000000 \
  --max-build-bytes 6000000000 --runtime-role integral_runtime
integral indexes status --check

# One-off maintenance shell/container only. Never put this variable in API env.
# INTEGRAL_MIGRATION_POSTGRES_DSN comes from the maintenance secret source.
integral indexes apply --plan /secure/index-plan.json --approved-hash REVIEWED_SHA256
```

The artifact binds the database identity (name, OID and server endpoint), catalog
revision, source-input and implementation hashes, expected starting definitions, operations, rollback
SQL, query coverage, estimates and count/steady/build-space budgets. Move an
artifact between containers only if the database endpoint binding remains the
same. `apply` reconstructs the selected source catalog and rejects changes or
unexpected index drift. A hash proves the reviewed artifact's identity; review
and secret distribution remain operator responsibilities, not an approval service.

Matching applied receipts are retained without rewriting them on every deployment.
Two fixed relational metadata tables hold catalog receipts and migration progress,
independently of graph initialization. Their bootstrap DDL is included in the plan;
their primary keys are catalog members included in the count and byte budgets.
The runner takes a database-wide advisory lock, builds one index at a time, stores
intent before each operation, and reconciles actual state on restart. Concurrent
build/drop operations run outside transaction blocks. Valid/ready state and full
expression/order/collation/opclass semantics matter: a name alone is insufficient.
An explicit `--repair NAME` plans `REINDEX INDEX CONCURRENTLY` for a matching invalid
index after duplicate/data problems have been resolved. A failed unique build is
never acknowledged as ready. See [PostgreSQL concurrent builds](https://www.postgresql.org/docs/16/sql-createindex.html).

The estimate is conservative rows × keys × 96 bytes, not a capacity guarantee.
Qualify estimates on a populated disposable copy. Each build also caps backend scratch files with `temp_file_limit` after reserving
existing index bytes and the new-index estimate. The migration role needs `SET`
privilege on that parameter; serving roles do not. Actual budget excess blocks
further provisioning and appears in `status --check` and runtime warnings;
constraints are not removed and valid business writes are not disabled for it.
Observe pg_stat_progress_create_index and the relational migration receipt for
progress, plus index bytes/count and budget/readiness warnings in monitoring.

## Fresh installs and recovery

`integral init`'s local supervisor provisions the release-reviewed catalog with
its private PostgreSQL owner before launching serving processes. Generated runtime
credentials are persisted in local private configuration. Existing homes lacking
these credentials must migrate explicitly rather than silently adopt ownership.
Use a reviewed plan with `--runtime-role`, configure that role/password as the
serving DSN, and retain the owner credential only in the maintenance secret source.
Business cell provisioning uses the selected API artifact in a separate one-off
container and retains its plan/runtime secret in the control-plane-only storage
volume. Swarm deployment applies an explicitly supplied plan before serving.

For existing databases: verify a restorable backup; inventory and adopt an inspected
baseline; build replacements on a disposable populated copy first. Unknown indexes
are preserved. A zero scan count is not removal evidence. `--remove NAME` requires
`--retirement-evidence FILE`: a mapping from name to `definition_hash`,
`replacement_fingerprints`, covered `queries` and a `qualification_sha256` for the
operator's query-plan/result/workload qualification report. Constraint-backed,
primary and unique indexes cannot be removed by this command. Coverage is checked
against registered replacements before removal. The evidence format intentionally
requires platform review; the CLI cannot prove that an arbitrary consumer is safe.
Rollback SQL recreates retired definitions and can take substantial time. Retain
the original artifact and relational progress; retry the same artifact to resume.
Physical storage plans are separate from App record-data migrations.

## App proposals and privileged hosts

An App may declare `app.storage_index_requests_version: 1` and at most 16
`storage_index_requests` containing `key`, `query_key`, `track_key`,
`entry_type_key`, `fields: [{field, direction}]` and `reason`. Queries and typed
fields must already be declared. Compiled proposals have
`state: pending_platform_review`. Raw SQL, predicates, tables and physical names
are rejected. The optional package capability is
`{type: storage_index_requests, config: {version: 1}}`.

A proposal never provisions storage or grants access. Platform reviewers supply
qualified `IndexSpec` definitions, bound to App/query/package revision and a
qualification-report hash, using `--approved-app-catalog FILE`, `--app-manifest FILE`
and `--include-request slug:key`. The artifact binds every input file hash.
Arbitrary field annotations alone cannot prove index width safety or the exact
SQL expression used by a handler, so the compiler does not guess physical indexes.

A privileged host can configure `INTEGRAL_STORAGE_CATALOG_PROVIDERS=module:function`.
The callable returns version 1, owner, model classes and frozen approved fingerprints.
Required unapproved host declarations fail; optional unapproved ones stay proposals.
This is trusted host code, never App/tenant metadata. Every process and migration
must select the same providers. Business's provider owns billing integrity.

## Qualification gate

Run foundation PostgreSQL contracts, Core-only isolation, lifecycle tests with
`STORAGE_INDEX_TEST_DSN` pointing only to disposable PostgreSQL, affected Business
suites, frontend checks and candidate-wheel import proofs. Check runtime roles
cannot CREATE/DROP/ALTER; failed builds, crash/resume and competing runners;
permissions, event recovery, pagination and search quality; and pending proposals.

Use mixed 10k/100k/1m datasets and verify that records, workspaces, saves, searches
and restarts do not increase physical index count. Record index bytes, reads,
WAL and ≥100 saves at one/ten users, cold runs and worker backlog/slow delivery.
API/adapter write timing is not complete-browser invoice timing. The invoice
acceptance target remains browser p95 <1 second on an agreed representative
populated dataset. Qualification results and remaining coverage must be reported;
a fresh/synthetic database cannot certify the previous populated workload.
