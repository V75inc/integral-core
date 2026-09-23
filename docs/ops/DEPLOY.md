# Integral — Deployment runbook

> **The Fly.io + Cloudflare Pages topology below was never provisioned, and its
> CI has been removed.** `gointegral.app` and `api.gointegral.app` do not
> resolve; no `FLY_API_TOKEN` or `CLOUDFLARE_*` secret ever existed, so
> `deploy-backend.yaml` and `deploy-frontend.yaml` failed on every push to
> `main` from the day they landed. They are deleted, along with
> `backend/fly.toml`, `frontend/public/_redirects`, and
> `frontend/public/_headers`.
>
> **What actually deploys is `.github/workflows/deploy.yml`** — build to a
> Docker registry, then SSH to a Swarm host — for `dev`, `main`, and `prod`
> against `deploy/docker-stack.*.yml`. Its secrets (`REGISTRY_PASSWORD`,
> `SSH_PRIVATE_KEY`, `*_ENV_FILE`) are configured and green.
>
> Sections marked **(historical)** describe the unbuilt Fly/Cloudflare plan.
> They are kept because the intent is worth having on record, not because they
> reflect anything running. **Backups**, **Restore, and the drill**, and
> **Atlas Vector Search** are current and apply to the Swarm deployment.

## Topology (historical)

```
┌──────────────────────────┐        ┌─────────────────────────────┐
│  Cloudflare Pages        │        │  Fly.io (iad)               │
│  gointegral.app           │        │  api.gointegral.app          │
│                          │        │                             │
│  React SPA (Vite build)  │ HTTPS  │  FastAPI + jvspatial        │
│   /api/* → rewrite ──────┼───────▶│  /api/*                     │
│   /* → index.html        │        │  /health                    │
│                          │        │                             │
└──────────────────────────┘        └──────────────┬──────────────┘
                                                   │
                                                   │ TLS
                                                   ▼
                                       ┌────────────────────────┐
                                       │ MongoDB Atlas          │
                                       │ integral_prod          │
                                       └────────────────────────┘
```

- **Frontend**: Cloudflare Pages, project `integral`, build dir
  `frontend/dist`. SPA fallback + `/api/*` rewrite to the Fly app are
  configured by `frontend/public/_redirects` and `frontend/public/_headers`.
- **Backend**: Fly.io app `integral-api` in `iad`. Config at
  `backend/fly.toml`. 2× shared-cpu / 1 GB machines (`min_machines_running = 2`)
  so rolling deploys do not drop in-flight requests; scales horizontally
  on demand. **(Historical — contradicts the live posture: the Swarm stacks
  pin `WEB_CONCURRENCY: "1"` per ADR-005; see § Single worker below.)**
  `WEB_CONCURRENCY=2` runs two uvicorn workers per VM to
  amortize CPU-bound work (LibreOffice preview, ffprobe, libmagic).
- **Database**: MongoDB Atlas — M10 dedicated cluster (M0 free tier is
  acceptable for previews only; it has no IP allowlisting beyond
  `0.0.0.0/0`, no automated backups, and is single-node).
- **Attachment storage**: Cloudflare R2 bucket `integral-attachments`,
  accessed via the S3 API. The backend VM filesystem is ephemeral, so
  local storage MUST NOT be used in production — uploads would be lost
  on every redeploy.
- **Email**: Resend, sending from `noreply@gointegral.app`.
- **Errors**: Sentry, environment `production`. DSN is optional;
  enabling it requires the project to exist in the Sentry org.

## DNS (historical)

Owner: Cloudflare DNS for `gointegral.app`.

| Record | Name | Value | TTL |
|--------|------|-------|-----|
| CNAME  | `@`  | (Cloudflare Pages target — set automatically) | Auto |
| CNAME  | `www` | `gointegral.app` | Auto |
| A/AAAA | `api` | (set by `fly certs add api.gointegral.app` — copy from `flyctl ips list`) | Auto |
| MX, TXT | (Resend) — set per Resend onboarding for sending domain | | |
| TXT (DKIM, SPF, DMARC) | per Resend instructions | | |

Verify after DNS propagates:

```bash
dig +short gointegral.app
dig +short api.gointegral.app
fly certs check api.gointegral.app  # waits until issued
```

## Secrets (historical)

### Fly (backend)

```bash
cd backend
flyctl secrets set \
  SECRET_KEY="$(openssl rand -hex 32)" \
  JVSPATIAL_DB_TYPE=mongodb \
  JVSPATIAL_MONGODB_URI="mongodb+srv://<user>:<pwd>@<cluster>.mongodb.net/?retryWrites=true&w=majority" \
  JVSPATIAL_MONGODB_DB_NAME=integral_prod \
  RESEND_API_KEY="re_..." \
  ADMIN_EMAIL="eldon.marks@v75inc.com" \
  ADMIN_PASSWORD="$(openssl rand -base64 24)"

# Attachment storage (Cloudflare R2 via S3 API). REQUIRED in production —
# the Fly VM filesystem is ephemeral. Without these, uploads are lost on
# every redeploy. See "Attachment storage (Cloudflare R2)" below.
flyctl secrets set \
  JVSPATIAL_FILE_STORAGE_PROVIDER=s3 \
  JVSPATIAL_S3_BUCKET_NAME=integral-attachments \
  JVSPATIAL_S3_REGION=auto \
  JVSPATIAL_S3_ENDPOINT_URL="https://<account-id>.r2.cloudflarestorage.com" \
  JVSPATIAL_S3_ACCESS_KEY="..." \
  JVSPATIAL_S3_SECRET_KEY="..."

# Optional:
flyctl secrets set SENTRY_DSN="https://..."
```

`flyctl secrets set` triggers an automatic redeploy. Use
`flyctl secrets list` to verify.

### Cloudflare Pages (frontend)

The static bundle does not currently consume any secrets at build
time — `VITE_API_URL` is left empty so the SPA calls same-origin
`/api/*` and the rewrite forwards to Fly. If telemetry or feature
flags are added, register them in the Pages dashboard
(Settings → Environment variables) under both `Preview` and
`Production`.

### GitHub Actions

Repository → Settings → Secrets → Actions:

| Secret | Used by |
|--------|---------|
| `FLY_API_TOKEN` | `deploy-backend.yaml` (run `flyctl auth token` to mint) |
| `CLOUDFLARE_API_TOKEN` | `deploy-frontend.yaml` (token with **Account → Cloudflare Pages → Edit**) |
| `CLOUDFLARE_ACCOUNT_ID` | `deploy-frontend.yaml` (Cloudflare dashboard URL) |

## Attachment storage (Cloudflare R2) (historical)

R2 is the production backing store for attachment binaries. It is S3-API
compatible, has zero egress fees to Cloudflare Pages (where the SPA
lives), and is read by the backend via `JVSPATIAL_FILE_STORAGE_PROVIDER=s3`.

Provisioning:

1. Cloudflare dashboard → R2 → **Create bucket**, name `integral-attachments`.
2. R2 → **Manage API tokens** → **Create API token** with **Object Read & Write**
   scoped to the bucket. Copy the Access Key ID, Secret Access Key, and
   account-specific S3 endpoint (`https://<account-id>.r2.cloudflarestorage.com`).
3. Set the six `JVSPATIAL_S3_*` Fly secrets shown in the "Secrets" block above
   (`JVSPATIAL_FILE_STORAGE_PROVIDER=s3`, bucket name, region `auto`,
   endpoint URL, access key, secret key).
4. Lifecycle: configure an R2 object lifecycle rule to expire incomplete
   multipart uploads after 1 day to keep storage clean.

Verify after deploy:

```bash
# Upload a small file to a Track entry via the API, then check that the
# attachment URL serves the bytes back. Trace the request in Fly logs to
# confirm the S3 PutObject call goes to the R2 endpoint, not local FS.
flyctl logs -a integral-api | grep -i 's3\|r2\|attachment'
```

## First-time provisioning (historical)

1. **Atlas**: create the cluster, create database user
   `integral_app`, allowlist Fly egress IPs (or `0.0.0.0/0` to start),
   capture the SRV connection string.
2. **R2**: create the `integral-attachments` bucket and API token as
   described in "Attachment storage (Cloudflare R2)" above.
3. **Fly**:
   ```bash
   cd backend
   flyctl launch --no-deploy --copy-config --name integral-api --region iad
   flyctl secrets set ...           # see above
   flyctl deploy
   flyctl certs add api.gointegral.app
   ```
4. **Cloudflare Pages**:
   - Connect the GitHub repo, project name `integral`,
     production branch `main`, build dir `frontend`,
     build command `npm ci && npm run build`,
     output dir `dist`.
   - Add `gointegral.app` and `www.gointegral.app` as custom domains.
5. **Transactional email** (pick one provider):
   - **Resend** (default on Fly): verify the sending domain (`gointegral.app`),
     add the DKIM/SPF records to Cloudflare DNS, mint an API key,
     set `EMAIL_PROVIDER=resend` and `RESEND_API_KEY` on Fly.
   - **SendGrid** (alternative): authenticate the sender domain in SendGrid,
     add the DKIM/SPF records to DNS, create an API key with **Mail Send**
     permission, set `EMAIL_PROVIDER=sendgrid` and `SENDGRID_API_KEY` on Fly
     (or in `deploy/.env` for Swarm). Also set `EMAIL_FROM`, `EMAIL_FROM_NAME`,
     and `APP_BASE_URL` to match the verified domain.
6. **Sentry** (optional): create project `integral-backend`, copy
   DSN, set `SENTRY_DSN` on Fly.

## Backups

> **This section previously described MongoDB Atlas snapshots, which do not
> apply.** The Swarm stack (`deploy/docker-stack.prod.yml`) runs an in-stack
> **Postgres/pgvector** on a single replica backed by a local `postgres_data`
> volume. Until `scripts/pg_backup.sh` landed there was no backup of the
> primary datastore at all — losing that volume meant losing the graph.

### Database (Postgres)

After deploying a backend image that pins **jvspatial ≥ 0.0.18**, run the
idempotent hub-adjacency scrub once per environment (see
[`docs/superpowers/specs/integral-deployment-playbook.md`](../superpowers/specs/integral-deployment-playbook.md)
and `deploy/scripts/strip_node_edges.sh`). Delete any leftover
`JVSPATIAL_NODE_EDGE_IDS` from runtime env (removed in jvspatial 0.0.19).

`scripts/pg_backup.sh` takes a verified, compressed `pg_dump` and prunes old
dumps. It writes to `.part` and only promotes the file after `pg_restore
--list` can read it, so a truncated dump is never mistaken for a good one.

```bash
# on the Swarm host, with POSTGRES_* from the deploy environment
BACKUP_DIR=/var/backups/integral BACKUP_RETAIN=14 scripts/pg_backup.sh
```

Schedule it (cron / systemd timer) at an interval no longer than the data loss
you are willing to accept — **the dump is point-in-time-of-run, not continuous
archiving**. If the acceptable window is smaller than the schedule interval,
this is the wrong tool: use WAL archiving (`wal-g`, `pgBackRest`) or a managed
Postgres with PITR. That is an infrastructure decision that has not been made
yet; the dump bounds the exposure in the meantime.

Copy dumps **off the host**. A backup on the same volume as the database does
not survive the failure it exists for.

### Restore, and the drill

```bash
scripts/pg_restore.sh BACKUP_FILE --drill      # rehearse: scratch DB, counts, drop
scripts/pg_restore.sh BACKUP_FILE --into copy  # restore beside the live DB
scripts/pg_restore.sh BACKUP_FILE              # in place — DESTRUCTIVE, confirms first
```

**Run the drill quarterly.** A backup nobody has restored is an untested
assumption. `--drill` never touches the live database: it creates a scratch
database, restores into it, and drops it. The drill fails if restored
`node`, `edge`, and `object` counts differ, or if OperationalModel version
and name or Attachment content hash, size, and storage key differ. File
bytes behind a storage key live on the file volume, which is backed up
beside this dump.

Both scripts prefer the `pgvector/pgvector:pg16` container for the client
binaries rather than whatever `pg_dump` is on the host — a client older than
the server refuses to run, and a host's package manager routinely lags.

### Other

- **App config**: this repository. Deploy secret values are NOT in git; back up
  the secret list (names only, never values) to a separate private vault
  whenever rotated.
- **Audit log**: `ChangeEvent` records live in the same Postgres database, so
  the dump covers them.

## Deploy

`.github/workflows/deploy.yml` runs on every push to `dev`, `main`, and `prod`,
and on manual dispatch with an environment picker. It builds both images, pushes
them to the registry, then SSHes to the target Swarm host and updates the stack
from `deploy/docker-stack.<env>.yml`:

| Branch | Stack | Compose file |
|--------|-------|--------------|
| `dev`  | `integral-dev`  | `deploy/docker-stack.dev.yml` |
| `main` | `integral-main` | `deploy/docker-stack.main.yml` |
| `prod` | `integral-prod` | `deploy/docker-stack.prod.yml` |

Routing is Traefik, keyed off `DOMAIN` (SPA) and `DOMAIN_API` (API) from the
environment's `*_ENV_FILE` secret. `VITE_API_URL` is baked into the web image at
build time, so a domain change requires a rebuild, not just a restart.

Deploys are serialized per ref (`concurrency: deploy-${{ github.ref }}`,
`cancel-in-progress: false`), so a burst of merges queues rather than racing.
A run showing `cancelled` in the Actions list is a superseded *older* push,
which is normal — it is not a failure.

### Worker concurrency (keep at 1)

The API image runs `uvicorn --workers ${WEB_CONCURRENCY}`
(`backend/Dockerfile`; default **1**). Local / non-Docker boots use
`WORKERS` via `python -m app.main`. Either value above 1 breaks chat
invariants (per-process turn registry + websocket fan-out) — see
[ADR-005](../backend/adr/005-single-worker-until-shared-turn-state.md).
`app/main.py` warns at boot when `max(WORKERS, WEB_CONCURRENCY, 1) > 1`.
Do not raise either knob until turn state is shared. The deployable stacks
now pin `WEB_CONCURRENCY: "1"` and the api-env overlay denies
`WEB_CONCURRENCY`/`WORKERS`, so raising it is a deliberate stack-file edit.

> **Log DB is ephemeral by design (for now).** Every deployable stack sets
> `JVSPATIAL_LOG_DB_PATH=/tmp/integral_logs.db`, which is wiped whenever the
> task is replaced (deploy, reschedule, crash). ChangeEvents used for in-app
> audit surfaces live in Postgres and survive; what dies with the task is the
> supplementary jvspatial log store. It stays on `/tmp` because the
> `integral_data` volume is root-owned on first create (see the comment in
> `docker-compose.local.yml`); move it onto a volume or ship it externally
> before treating those logs as an audit trail.

### Observation budgets under `JVAGENT_UPDATE_MODE=merge`

> **One-time staging note (2026-08-12):** the main stack's fallback moved
> `merge` → `source`. The FIRST staging deploy after this rebuilds action
> nodes from `agent.yaml` and **discards any runtime drift tuned live on
> staging** (prompt/model tweaks applied via API). That is the point — staging
> tracks the repo — but if you had deliberate live-tuned staging state, export
> it before deploying.

The **prod** stack defaults to **`merge`** so runtime drift on action nodes
survives restarts (`deploy/docker-stack.prod.yml`,
`deploy/docker-compose.prod.yml`). The **main (staging)** and **dev** stacks
default to **`source`** — staging exists to exercise `agent.yaml` as shipped,
so its action-node config tracks the repo on every deploy; before the main
stack's fallback agreed with `.env.main.example`, an operator who skipped
copying the example silently got `merge` and staging showed stale budgets
while looking current. Note the api-env overlay DENIES `JVAGENT_UPDATE_MODE`
(`deploy/ci-generate-api-env-overlay.sh`), so for Swarm deploys the value in
the env-file secret reaches the stack only through shell interpolation at
`docker stack deploy` time — present wins, absent falls back to the stack
default. Under merge, edits to
already-registered action context in `agent.yaml` (including
`observation_max_chars`, `stale_observation_max_chars`,
`observation_full_recent`) are **silently ignored** — the persisted node
keeps its first-registered values. A distro
`agent.override.yaml` is how a pip install changes persona, model, and
budgets without editing the wheel. Under merge that file is ignored the
same way, and boot logs a warning. The allowlist is in the
[quick start](../developer/quickstart.md#resident-agent-override).

**One-time path to land YAML budgets on an existing environment** (do not
flip the permanent prod default to `source` without accepting the risk
below):

1. Confirm the values you want in
   `agent/agents/integral/integral_agent/agent.yaml`, or in a distro
   `agent.override.yaml` for a pip install (and that CI /
   `test_orchestrator_perf_config.py` still floors the shipped file).
2. Set `JVAGENT_UPDATE_MODE=source` for a **single** API bootstrap (one
   deploy or one process restart with the override in the env file /
   stack), **or** write the attributes onto the orchestrator action node
   directly.
3. Confirm boot no longer shows stale budgets (or inspect the action node).
4. Restore `JVAGENT_UPDATE_MODE=merge` for ongoing multi-worker / prod
   posture.

**Risk of leaving `source` on:** every restart rebuilds action nodes from
YAML and **discards** runtime drift (prompt/model tweaks applied via API
or prior merges). That is correct for greenfield / local, wrong for prod
where merge is intentional. Prefer the one-shot source boot (or a direct
node write) over changing the stack default. Background:
[docs/backend/ai-chat.md](../backend/ai-chat.md) (observation budgets),
[agent/README.md](../../agent/README.md).

### Ownerless App `OWNS` backfill

Apps missing a User→App `OWNS` edge are administratively dead (403 on
track create / collaborator / operational-model paths). Repair with:

```bash
cd backend
# same JVSPATIAL_* as the target environment
python scripts/backfill_app_owner_grants.py --dry-run
python scripts/backfill_app_owner_grants.py
```

**Run this from a checkout, not from the API container.** `backend/Dockerfile`
copies `backend/app` into the product image — `backend/scripts/` is NOT
there, so `docker exec <api-task> python
scripts/backfill_app_owner_grants.py` fails with "No such file or directory".
Use a host that has the repo and export the target environment's `JVSPATIAL_*`
values (the DSN in particular) so the script talks to the same database the
service does.

Core-only image (no domain Apps on disk):
`docker build --target core -f backend/Dockerfile .` (sets `INTEGRAL_CORE_ONLY=1`).
Default / compose builds remain the full product image.

Staging → dry-run → apply → smoke, then the same against prod. Unresolved
Apps exit non-zero — decide manually. Full procedure is in the script
docstring.

## SPA security headers

`frontend/nginx.conf` is what the web image serves, and it sends the SPA's
security headers: `Content-Security-Policy`, `Strict-Transport-Security`,
`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`,
`Permissions-Policy`, `Cross-Origin-Opener-Policy`.

They previously lived only in `frontend/public/_headers` — a Cloudflare Pages
convention nginx never reads — for a Cloudflare deploy that was never
provisioned, so **no deployed environment had ever sent them**.

### connect-src is derived, not configured

Nothing to set per environment.
`frontend/docker-entrypoint.d/15-csp-connect-default.envsh` derives
`https://<origin> wss://<origin>` from the `VITE_API_URL` build arg — the same
value baked into the SPA bundle by `deploy/ci-resolve-vite-api-url.sh`. The
policy therefore cannot drift from the origin the app actually calls, which is
the failure a duplicated hostname guarantees eventually.

Correct in both routings with no special case:

| routing | `VITE_API_URL` | rendered |
|---|---|---|
| split-host | set | `connect-src 'self' https://api… wss://api…` |
| same-origin | empty | `connect-src 'self'` |

`CSP_EXTRA_CONNECT` in an env file still wins, and **replaces** the derived
value — so if you set it, include the API origin too. Never set it to an empty
string: that overrides the default with nothing and blocks every XHR and the
`/api/events` WebSocket.

Two mechanics worth knowing before editing any of this:

- The `.envsh` must stay **executable**. nginx's entrypoint sources one only
  `if [ -x "$f" ]` and otherwise logs `not executable, ignoring`. A 0644 copy
  is skipped silently and `connect-src` renders as the literal
  `${CSP_EXTRA_CONNECT}` — green container, broken policy. Hence
  `COPY --chmod=0755` in `frontend/Dockerfile`.
- Every header is set at **server** level. nginx's `add_header` inheritance is
  all-or-nothing: one `add_header` inside a `location` discards every inherited
  one, so a per-location `Cache-Control` on `/assets/` would strip the whole
  security set from every JS and CSS response while `index.html` looked fine.
  Cache-Control comes from a `map` for that reason.

### Inline scripts are hash-allowlisted

`frontend/index.html` carries two inline `<script>` blocks that must run before
first paint. They are allowed by sha256 rather than `unsafe-inline`. Editing
either by one character invalidates the hash and the browser silently refuses
to run it — a wrong-theme flash for one, a **white screen** for the other, and
no build, type check or unit test catches it.
`.ci/csp_inline_script_hash_check.sh` recomputes the hashes and fails on drift;
it runs on the pre-commit hook and in CI, and scans the working tree rather
than the staged index.

## Verify after deploy

Substitute the environment's `DOMAIN` / `DOMAIN_API`.

**The two health routes sit at different prefixes**, which is easy to get
wrong: `/health` is jvspatial's built-in liveness route and is *not* under
`/api`, while `/health/ready` is one of our own `@endpoint`s and therefore is.
Verified against a running server — `/health/ready` returns **404**.

```bash
curl -fsS  "https://$DOMAIN_API/health"             # liveness (jvspatial built-in)
curl -fsS  "https://$DOMAIN_API/api/health/ready"   # readiness (app @endpoint)
curl -fsSI "https://$DOMAIN_API/health" | grep -i 'strict-transport-security'
```

Before the manual pass, run the external verifier — it turns the checklist
below into exit codes and catches every green-healthcheck trap this repo has
hit (missing headers, literal `${CSP_EXTRA_CONNECT}`, split-host/VITE_API_URL
mismatch, API routed to the static nginx):

```bash
deploy/verify-live-env.sh $DOMAIN $DOMAIN_API   # omit DOMAIN_API for same-origin
```

Its header documents the two SSH-only checks (observation-budget WARNING,
boot-guard crash-loop on a placeholder JWT).

Then visit `https://$DOMAIN/login`, sign in with the bootstrap admin, create a
Track, post an Entry, request a password reset on the admin email, and confirm
the email arrives.

> **The SPA host sets the security headers — this paragraph used to say it did
> not.** That was true when `frontend/public/_headers` (Cloudflare-Pages-only)
> was the only place they lived, and it stopped being true when the header set
> was ported into `frontend/nginx.conf`. What the web image serves today, at
> SERVER level so it applies to every response including 404/50x: `CSP`,
> `HSTS`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
> `Permissions-Policy`, `Cross-Origin-Opener-Policy`, plus `Cache-Control` via
> a `map` rather than a per-location `add_header` (nginx's `add_header`
> inheritance is all-or-nothing — one `add_header` inside a `location` discards
> every inherited one, which would have silently stripped the whole set from
> every JS and CSS response).
>
> `connect-src` is derived from the `VITE_API_URL` build arg by
> `frontend/docker-entrypoint.d/15-csp-connect-default.envsh`, so the policy
> cannot drift from the app it protects; see the CSP section above for how to
> add an origin. Verify against the running service rather than the file:
>
> ```bash
> curl -sI https://$DOMAIN/ | grep -i -E 'content-security-policy|strict-transport'
> ```
>
> The backend's own responses remain covered by `SecurityHeadersMiddleware`.
> Residual: `style-src` still carries `'unsafe-inline'`.

## Atlas Vector Search (semantic / hybrid retrieval)

`POST /api/retrieve` runs in three modes — `graph`, `semantic`, and
`hybrid`. The semantic + hybrid paths require a vector store. On Postgres
deployments (the default) this is **pgvector** — the `vector` extension
on the same cluster as the rest of the data, backing an `entry_embedding`
table. On MongoDB Atlas deployments it is **Atlas Vector Search** on a
dedicated `entry_embeddings` collection. Graph-only deployments use the
`null` driver and the endpoint degrades semantic / hybrid → graph
transparently (`degraded=true` in the response).

### Resolution

`EMBEDDING_STORE_DRIVER` defaults to `auto`:

- `auto` + `JVSPATIAL_DB_TYPE=postgres` + `JVSPATIAL_POSTGRES_DSN` set
  → resolves to the `pgvector` driver.
- `auto` + `JVSPATIAL_DB_TYPE=mongodb` + `JVSPATIAL_MONGODB_URI` set
  → resolves to the `atlas` driver.
- `auto` + anything else → resolves to the `null` driver (graph-only).
- Explicit `pgvector` / `atlas` / `null` overrides the resolver.

### pgvector (Postgres deployments)

The `pgvector` driver opens its own asyncpg pool from
`JVSPATIAL_POSTGRES_DSN` and idempotently provisions its schema on first
use — `CREATE EXTENSION IF NOT EXISTS vector`, the `entry_embedding`
table (`vector(384)`, `track_id`, `type_id`, `deleted_at`), a B-tree
index on `track_id` (the `scope="track:<id>"` pre-filter), and an HNSW
index over `vector vector_cosine_ops` for cosine ANN search. No manual
index provisioning is required (unlike Atlas). NEW / updated entries embed
automatically via the `_reembed_entry` write hook; EXISTING entries need
the one-time backfill below.

Confirm the resolved state without shell access:

```bash
curl -fsS https://api.gointegral.app/api/retrieval/config \
  -H "Authorization: Bearer $TOKEN" | jq
# {
#   "embedding_model_eager_load": true,
#   "retrieve_k_default": 150,
#   "retrieve_top_n_default": 20,
#   "embedding_store_backend": "AtlasVectorDriver",
#   "semantic_available": true
# }
```

`semantic_available=false` means the deployment is on the `null`
driver — semantic / hybrid requests will return `degraded=true` with
graph-mode results.

### Index provisioning

`backend/app/main.py::_ensure_atlas_search_index` runs once at startup
when the `atlas` driver is active. It lists existing search indexes on
`entry_embeddings` and creates `entry_embedding_vector_idx` only if
absent. Atlas builds search indexes asynchronously — startup logs and
continues; the index hydrates in the background.

If the runtime lacks the `createSearchIndex` privilege (common on
restricted user roles), provision manually via the Atlas UI or CLI:

```json
{
  "name": "entry_embedding_vector_idx",
  "type": "vectorSearch",
  "definition": {
    "fields": [
      {
        "type": "vector",
        "path": "vector",
        "numDimensions": 384,
        "similarity": "cosine"
      },
      { "type": "filter", "path": "track_id" },
      { "type": "filter", "path": "deleted" }
    ]
  }
}
```

Apply via the Atlas CLI:

```bash
atlas clusters search indexes create \
  --clusterName integral-prod \
  --db integral_prod \
  --collection entry_embeddings \
  --file entry_embedding_vector_idx.json
```

Or in the UI: **Atlas → Cluster → Search → Create Index → JSON Editor**,
target `integral_prod.entry_embeddings`, paste the JSON above.

### Backfill

After provisioning (or any model / driver swap), hydrate the index
from canonical Entry data:

```bash
# Dry-run — compute embeddings, no writes.
flyctl ssh console -a integral-api -C "python -m scripts.backfill_embeddings --dry"

# Real backfill.
flyctl ssh console -a integral-api -C "python -m scripts.backfill_embeddings"
```

The script is idempotent (upserts by entry id — `ON CONFLICT` on
pgvector, `update_one` upsert on atlas), so it is safe to re-run. It
aborts with a non-zero exit if the resolved store is `null`. Scope it to
a workspace or track with `--workspace <id>` / `--track <id>`, and smoke
a small slice first with `--limit <n>`. A one-shot ECS task / Fly machine
in the production project is the cleanest way to drive it for large
datasets.

### Operational tunables

| Env var | Default | Purpose |
|---|---|---|
| `EMBEDDING_STORE_DRIVER` | `auto` | `auto` \| `pgvector` \| `atlas` \| `null`. |
| `EMBEDDING_MODEL_EAGER_LOAD` | `1` | Warm the ONNX model at startup. |
| `FASTEMBED_CACHE_PATH` | `/opt/fastembed_cache` | Pre-baked in the runtime image. |
| `RETRIEVE_K_DEFAULT` | `150` | Vector over-fetch budget. |
| `RETRIEVE_TOP_N_DEFAULT` | `20` | Returned-to-caller cap. |
