# Offline native storage upgrades

`integral storage-upgrade` adopts the reviewed managed-storage catalog and a
restricted serving role for an existing `desktop_single_user` home that predates
managed serving credentials. It is an explicit maintenance command, never a
normal-boot migration. Server and hosted database operators use the separate
`integral indexes` plan/apply workflow.

## Prepare and review

Stop the owned native runtime. Retain its previous wheel and a verified cold
snapshot of the entire home, including PostgreSQL, installation keys, attachments
and any client account state. Keep those files private. Never copy a live
PostgreSQL directory, remove process markers to bypass checks, or restore database
files separately from their attachments and keys.

Run the candidate's installed CLI with that same wheel selected. A composed
desktop wheel may replace UI resources; its maintenance engine must match the
executing Core engine exactly. Supply explicit count, resident-byte and build-byte
budgets appropriate to the installation:

```sh
integral storage-upgrade plan --home "$CORE_HOME" --wheel "$WHEEL" \
  --plan "$PLAN" --max-indexes "$INDEX_LIMIT" \
  --max-index-bytes "$RESIDENT_BYTE_LIMIT" --max-build-bytes "$BUILD_BYTE_LIMIT"
```

The command takes the ordinary native command and supervisor locks and starts
only the private PostgreSQL helper. No API, resident harness, worker or integration
starts. It refuses a running supervisor, a database PID/readiness marker, linked
database paths, or an unreconciled restored-home quarantine. It stops the helper
before returning. PostgreSQL performs ordinary startup and the helper reapplies
its saved administration password and local authentication configuration;
catalog planning itself is read-only and does not apply Core schema/index
changes or introduce serving credentials.

The private plan binds the canonical home, installation identity and configuration
digest, wheel digest, PostgreSQL cluster system identifier, database name and OID,
schema, catalog/compiler identity, operations, generated role name and budgets.
Review all of them. The private Windows database may bind a new loopback port
between planning and apply. The cluster and database identity must still match.
The printed SHA-256 covers the **whole plan file**, including the native
envelope; this differs from
`integral indexes apply`, whose approval names the inner catalog-plan hash.
Existing plans are never overwritten. Plans contain no database passwords.

## Apply, retry and rollback

With the home still stopped and its cold snapshot retained, run:

```sh
integral storage-upgrade apply --home "$CORE_HOME" --wheel "$WHEEL" \
  --plan "$PLAN" --approved-hash "$REVIEWED_WHOLE_FILE_SHA256"
```

Application rejects changed keys/configuration, another home or wheel, partial
runtime credentials, and destructive or repair operations. Duplicate identities
must be reconciled separately: this command never deletes records to make a
unique index succeed. A private `storage-upgrade-credential.json` journals the
generated role/password against the plan before role creation, allowing exact
retries after an interrupted index build. Retain that file with the home; never
put it in a review artifact or diagnostic output.

The new role must pass catalog readiness and privilege checks before the command
atomically adds `runtime_database_user` and `runtime_database_password` to
`installation.json`. All other installation values are retained. The JSON receipt
contains the inner plan hash, role and password digest; it contains no plaintext
credential. Subsequent managed startup verifies this catalog and role, without
creating a replacement plan. An already managed installation needs a separately
reviewed catalog upgrade, rather than this initial-adoption command.

On failure, keep the reviewed plan, journal and stopped failed home. An exact
retry can resume compatible completed operations. If a process or readiness
marker remains, stop and reconcile that owned process before retrying. For a
rollback, restore the verified **whole cold snapshot** and its previous wheel
together; keep the failed home for diagnosis. Do not perform an automatic schema
down migration or regenerate identity keys. Native updaters can wrap this command
with their existing snapshot, trial-start and whole-home rollback protocol.

## Qualification contract

Unit/smoke tests cover public CLI dispatch, wheel matching, explicit approvals,
budgets, quarantine, process ownership, linked paths and redacted failures.
`make test-postgres-ci` selects the populated PostgreSQL regressions, including
interrupted-build retry, key/attachment preservation and denied serving-role
DDL/catalog writes. Installed-wheel native API, client updater and supported-OS
installer tests remain release qualifications; the database tests alone do not
establish those results.
