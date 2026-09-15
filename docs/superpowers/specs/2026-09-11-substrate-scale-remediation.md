# Integral — Substrate Scale Remediation (Postgres + jvspatial usage)

**Date:** 2026-09-11
**Status:** Phase A **complete** (2026-09-13) — jvspatial 0.0.18 on PyPI; jvagent 0.1.8rc11 on TestPyPI; Integral pins from indexes (no path sources); `strip_node_edges` + `nodes_page`/`count_nodes` + `nodes_len` guard landed. **Follow-up (2026-09-14):** pin jvspatial 0.0.19 / jvagent 0.1.8rc12 — `JVSPATIAL_NODE_EDGE_IDS` removed (adjacency always edge-collection); drop leftover env keys from stacks. Phases B–G unchanged.

> **Follow-up (jvspatial 0.0.19):** `JVSPATIAL_NODE_EDGE_IDS` was removed — adjacency is always the edge collection. Keep `strip_node_edges` as data hygiene; delete leftover env keys from runtime.
**Repo:** Integral (`backend/`)
**Depends on:** jvspatial brief `2026-09-11-jvspatial-hub-scale-remediation.md` — Phases 1–3 there (adjacency off nodes, traversal pushdown, index hygiene) ship as jvspatial 0.0.18 (PR TrueSelph/jvspatial#44); hard cut of the dual-mode toggle ships as 0.0.19. Phases B, C, D-partial, E and G can proceed in parallel.
**Author:** Eldon Marks (brief prepared with Claude)

---

## How to use this brief

You are Claude Code working in the `integral` repo. Read `CLAUDE.md`, `docs/INVARIANTS.md` and ADR-005 first. Work phases in the order given unless a phase is marked parallel-safe. Each phase ends with a gate that must be green before the next starts. Substrate-touching changes (everything in Phases A, C, D, F, G) go through the plan-checker and must enumerate which invariants they preserve (I-GRAPH-01, I-GRAPH-02, I-CRUD-01, I-SUBSTRATE-01, I-ROLE-01..03). `make verify` is the commit gate; `make test-postgres` is additionally mandatory for every phase here because the failure modes are Postgres-specific and the SQLite fast lane will not show them. Never push without explicit instruction.

Where this brief cites a line number it is from the 2026-09-11 audit; verify the symbol before editing.

---

## Problem statement

A code-level audit (2026-09-11) of the backend against jvspatial 0.0.17's Postgres backend found that the foundation is sound in kind — one JSONB table per collection, functional B-tree indexes, an indexed `edge` table, denormalised `context.track_id` with DB-side keyset pagination on the hottest path — but that four usage patterns will stop scaling before the first large tenant does:

1. **Hub-node write amplification (jvspatial-side, fixed by jvspatial Phase 1).** `track.connect(entry, CONTAINS)` (`services/entry_create.py` ~L135) and `user.connect(notification, HAS_NOTIFICATION)` (`services/app_graph.py::link_notification` ~L643) each rewrite the hub node's whole JSONB document and serialise on its row lock. A 100k-entry track or a user with 50k notifications makes every create O(degree). Integral's part is to *adopt* the fix (Phase A) and to stop relying on `edge_ids` anywhere.
2. **Traversal call shape defeats the fast path (jvspatial Phase 2 + Integral adoption).** `CLAUDE.md`'s canonical `node.nodes(edge=[E], node=["T"])` list form makes jvspatial load every outgoing edge of every type, hydrate all neighbours, then filter and `limit` in Python. Once 0.0.18 lands the same call is one SQL round trip — Integral needs to pin it, replace `len(await x.nodes(...))` with `count_nodes()`, and use `nodes_page()` where a page is what is wanted.
3. **The accessible-tracks aggregate is O(tracks × ~10 round trips).** `services/permissions.py::get_user_accessible_tracks` (~L1042–1200) resolves `resolve_role` per candidate track and per app, then `api/tracks.py` list (~L88) paginates the full result in memory. A 500-track workspace is ~5,000 point queries per cold computation, hidden by a 20 s **per-process** TTL cache (`services/permissions_process_cache.py`) that cannot be coherent across replicas. This is the p99 cliff users will report first.
4. **Index/sort mismatch and unindexable search.** `services/pagination.py::DEFAULT_ENTITY_SORT` is `(updated_at DESC, id DESC)` but the only Entry compound index is `(track_id, created_at DESC)` (`models/nodes.py` ~L503) — so every page of a large track is an index range scan followed by an in-memory sort of the whole track. `services/entry_search.py::entry_search_query_clause` emits `$regex` on title and body with the user's text **unescaped** (a `(` in the search box is a Postgres regex error today; it is also unindexable). Most scalar lookups are already indexed on the models; a handful (invitation token redeem, attached-profile lookups, chat session lookup, app lifecycle filter) rely on the `entity` column alone.

Plus two posture items: ADR-005 pins one worker and one replica because the chat turn registry and websocket fan-out are module-level dicts, and both the jvspatial entity cache and the permissions process cache are in-process — so the API tier cannot scale horizontally today. And `ARCHITECTURE.md` (§ "Native Graph Storage", ~L40 and ~L867) claims partitioning and clustering the substrate does not have; the multi-tenant option the roadmap's T1 names (per-tenant partitioning) is undesigned.

## Goals

- p95 for `GET /entries?track_id=` page 1 and page 50 flat with respect to track size, on a 200k-entry track.
- Entry create p95 on a 200k-entry track within 1.5× of a fresh track.
- `GET /tracks` and the feed cold path bounded at ≤ 12 DB round trips regardless of track count, no reliance on a process cache for correctness *or* acceptable latency.
- Search indexed (`tsvector`), escaped, and bounded.
- A load harness in the repo with recorded baselines, run before every milestone close.
- A path to `WEB_CONCURRENCY > 1` and `replicas > 1` (ADR-005 resolved) with all caches either shared or removed.
- `ARCHITECTURE.md` and `ROADMAP.md` T1 stating what the substrate actually does and the chosen tenancy model.

## Non-goals

- Changing the access model semantics (I-ROLE-01..03, explicit-deny, cascade rules) — only their evaluation cost.
- Replacing jvspatial or Postgres.
- Frontend work beyond consuming any new pagination cursors.
- Connector-mirrored large external systems (ARCHITECTURE open question 2) — this brief makes the substrate ready for them; it does not design lazy materialisation.

## Decisions (locked)

| Topic | Choice |
|---|---|
| jvspatial adjacency | Edge collection only (jvspatial ≥0.0.19). No `JVSPATIAL_NODE_EDGE_IDS` toggle; `strip-node-edges` remains a deploy hygiene step for pre-0.0.18 volumes. |
| Canonical traversal | List form stays canonical in `CLAUDE.md` (0.0.18 makes it one round trip). Add: `count_nodes()` for counts, `nodes_page()` for pages; `len(await x.nodes(...))` becomes a drift-guard violation. |
| Permission aggregates | Set-based: ≤ a fixed number of edge/node queries per call, evaluated in Python with the existing rule functions. `resolve_role` stays the single-resource oracle; a new `resolve_roles_bulk` is its batch equivalent with parity tests. |
| Process caches | Correctness never depends on them. After Phase C the permissions process cache is deleted (not made shared) — the set-based path is cheap enough. The jvspatial entity cache moves to `JVSPATIAL_CACHE_BACKEND=redis` when replicas > 1 (jvspatial ships `cache/redis.py`). |
| Search | `$text` + `@fulltext_index(["title","body"])` on `Entry` once jvspatial Phase 3 lands; until then `$regex` with `re.escape`. |
| GIN | `JVSPATIAL_PG_GIN_INDEX=off` once an audit shows no `$all`/`$elemMatch`/containment queries (Phase D.4). |
| Tenancy | Decision required from Eldon at Phase F — options laid out there. Not blocking A–E. |

---

## Phase A — Adopt jvspatial 0.0.18 (blocked on jvspatial Phases 1–3)

**Files:** `backend/pyproject.toml`, `backend/uv.lock`, `deploy/*.yml`, `deploy/.env.*.example`, `backend/.env.example`, `CLAUDE.md`, `docs/INVARIANTS.md`, `.ci/*` guards, all `len(await …nodes(` sites.

1. Pin `jvspatial==0.0.18` (then 0.0.19); `uv lock`; `make verify`; `make test-postgres`.
2. *(Superseded by 0.0.19)* Do **not** set `JVSPATIAL_NODE_EDGE_IDS` — the key is removed. Keep `deploy/scripts/strip_node_edges.sh` and document it in `docs/superpowers/specs/integral-deployment-playbook.md`. Delete leftover `JVSPATIAL_NODE_EDGE_IDS` from runtime env on roll.
3. Grep for any Integral code reading `edge_ids` / `["edges"]` directly (audit found none in `app/`, verify `agentive/` and `tests/`); remove or route through `edges()` / `connection_count()`.
4. Replace every `len(await <node>.nodes(...))` with `await <node>.count_nodes(...)`. Add `.ci/nodes_len_drift_check.sh` (pattern `len\(\s*await\s+[\w.]+\.nodes\(`) to the pre-commit guards and `make verify`.
5. Where an endpoint returns a *page* of neighbours (attachments, comments, catalog listings — grep `nodes(edge=[` in `api/attachments.py`, `api/content_profiles.py`, `api/apps.py`), switch to `nodes_page()` with the cursor threaded to the response the same way `paginate_entity_find` does. Do not change wire shapes that clients already consume without a frontend ticket.
6. `CLAUDE.md` § jvspatial primitive reference: add `count_nodes` / `nodes_page` rows; note that list-form filters push down as of 0.0.18; note `edges[]` is no longer persisted and `connection_count()` is the degree query.
7. Set `JVSPATIAL_POSTGRES_MAX_POOL_SIZE` explicitly in the prod stack (default 10 is fine for one worker; Phase E revisits).

**Gate:** `make verify` + `make test-postgres` green; `tests/test_perf_query_budget.py` budgets **tightened** to the new observed counts (they should drop; record the deltas in the test comments as the existing tests do); Phase B harness (if already built) re-run and numbers appended.

---

## Phase B — Load harness and baseline (parallel-safe; start immediately on 0.0.17)

**Why.** The audit was a code read. Every remediation below must cite before/after numbers, and the existing `test_perf_query_budget.py` counts round trips on tiny fixtures — it cannot see cost that grows with data.

**Deliverables**

1. `backend/tests/load/README.md` — how to run against `docker-compose.local.yml` Postgres (`pgvector/pgvector:pg16`, host :5433), env knobs, expected wall time.
2. `backend/tests/load/seed.py` — seeds through the **service layer** (not raw SQL, so I-GRAPH-01 wiring is real): 5 organisation workspaces; 500 users (100 per org, mixed admin/member/guest); 2,000 tracks across ~200 apps; 2,000,000 entries with a realistic skew — one hub track with 200,000 entries, ten tracks with 20,000, the rest ≤ 1,000; 50,000 notifications on one user; comments on 10 % of entries; collaborators/exclusions on 5 % of tracks. Use jvspatial `bulk_save_detailed` for leaf entries but still wire `CONTAINS` (batch the edge inserts). Seeding must be resumable and must print its own duration. Target ≤ 30 min on a laptop.
3. `backend/tests/load/bench.py` — async httpx client against a running server (`BASE_URL`), authenticating as three personas (org admin, member with 300 accessible tracks, guest with 3). For each, 50 iterations, p50/p95/p99 and `X-DB-Round-Trip-Count` (from `middleware/perf_header.py`) of:
   - `POST /entries` into the 200k hub track and into a fresh track;
   - `GET /entries?track_id=<hub>` page 1, page 50 (follow `next_cursor`), and with `q=` (search);
   - `GET /entries` workspace-wide (no track_id) for each persona;
   - `GET /tracks` cold (restart server or set `PERMISSION_PROCESS_CACHE_TTL=0`) and warm;
   - `GET /feed`;
   - `GET /notifications` for the 50k-notification user;
   - `GET /entries/{id}` for a deep entry (resolve_role full cascade);
   - `GET /apps/{id}/access` for an app with 40 collaborators.
   - Concurrency: 32 parallel `POST /entries` into the hub track; report total wall and max single latency.
4. `docs/bench/2026-09-substrate-baseline.md` — recorded numbers on 0.0.17, machine described. Commit them.
5. `make bench` (seed + run) and `make bench-run` (run only) targets; the harness is excluded from the default `pytest` collection.

**Gate:** Baseline document committed. Expected findings to confirm (do not assume): hub-track create latency ≫ fresh-track; `/entries` page 50 on the hub track ≫ page 1; `/tracks` cold ≫ warm by an order of magnitude for the 300-track persona; `/notifications` and `/tracks` for the 50k-notification user both slow (the `user.nodes(edge=["OWNS"], …)` calls load the notification edges).

---

## Phase C — Set-based permission aggregates (parallel-safe on 0.0.17; simpler on 0.0.18)

**Files:** `services/permissions.py`, `services/workspace_permissions.py`, `services/permissions_process_cache.py`, `middleware/permissions_cache.py`, `api/tracks.py`, `api/apps.py`, `services/entry_listing.py`, `tests/test_resolve_role_bench.py`, `tests/test_perf_query_budget.py`.

### C.1 `resolve_roles_bulk`

Add `async def resolve_roles_bulk(user_id, resource_type: Literal["app","track"], resource_ids: list[str]) -> dict[str, Optional[str]]` in `services/permissions.py`. It must produce exactly what `resolve_role` produces for each id (parity is the test), using a **bounded** number of queries independent of `len(resource_ids)`:

1. Load the user once (`get_user_node`).
2. One edge query for all direct grants: `edge.find({"source": user.id, "target": {"$in": ids}, "entity": {"$in": ["OWNS","COLLABORATES_ON","EXCLUDED_FROM"]}})` — index `(source, target, entity)`.
3. One node batch for the resources (`Track.find({"id": {"$in": ids}})` / `App.find(...)`), giving `workspace_id`, `visibility`, `template_id`, `lifecycle_state`.
4. One edge query for parent apps of the tracks (`edge.find({"target": {"$in": track_ids}, "entity": "CONTAINS"})`, then one `App.find({"id": {"$in": parent_ids}})`) — for the cascade level. Apps' own cascade (workspace) needs no further hop.
5. One `IS_MEMBER_OF` query for the user across the distinct workspaces involved; one `Workspace.find({"id": {"$in": …}})` for owner checks (`_is_workspace_owner_user`) and kinds.
6. One edge query for the user's direct grants/exclusions on the **parent apps** (same shape as step 2).
7. Evaluate in Python per resource with the *same* helper semantics as `resolve_role` (`_direct_role_on_resource`, `_is_excluded_from_resource`, `_visibility_grant_role`, `workspace_staff_implicit_resource_role`, `_cap_inherited_role`, public-readability) — refactor those helpers so each has a pure `_from_loaded(...)` variant that takes the pre-loaded edges/nodes, and have the single-resource versions call the pure variant after loading. That guarantees parity by construction.

Round-trip budget: ≤ 8 for any batch size. Mark the deviation inline per `CLAUDE.md` (`# deviation: bulk edge materialisation instead of per-resource cascade — measured <before> vs <after> on tests/load`) once Phase B numbers exist.

### C.2 Rewrite `get_user_accessible_tracks` / `get_user_accessible_apps`

Candidate generation becomes set-based too:

- direct: `edge.find({"source": user.id, "entity": {"$in": ["OWNS","COLLABORATES_ON"]}})` filtered to targets whose entity is Track/WorkspaceApp — **not** `user.nodes(edge=["OWNS"], node=["Track"])`, which (on 0.0.17) loads every `HAS_NOTIFICATION` edge; on 0.0.18 `user.nodes(edge=[OWNS, COLLABORATES_ON], node=[Track, WorkspaceApp])` is acceptable and is one round trip.
- app cascade: `Track.nodes_bulk([app_ids], edge=["CONTAINS"], node=["Track"])` (already used at ~L1128).
- staff / member-visibility inventories (`workspace_permissions.py` ~L174–243) already use `App.find` / `Track.find` by `workspace_id` — keep, but add the `(workspace_id)` indexes in Phase D so they are index probes.
- `_track_listable` (~L1075) resolves parent apps per track; replace with one `nodes_bulk` over all candidate tracks in the `"in"` direction plus `resolve_roles_bulk` over the parent apps.
- Final confirmation: `resolve_roles_bulk(user_id, "track", candidate_ids)`.

Then delete `services/permissions_process_cache.py` and its call sites; keep the per-request memo (`middleware/permissions_cache.py`) — it is request-scoped and safe under replicas. Keep `invalidate_user_accessible_caches` as a no-op shim for one release or remove with its callers.

### C.3 `/tracks` and `/apps` list pagination

`api/tracks.py` list (~L80–120) fetches every accessible track then paginates in memory. Acceptable once C.2 is bounded; but move the sort + cursor slicing into `services/pagination.py` helpers so the wire contract is unchanged and the in-memory step is a single sort over ids. Document that a DB-side page here would require materialising the access set — out of scope until a tenant needs > 5,000 accessible tracks per user.

### C.4 Tests

- `tests/test_resolve_roles_bulk_parity.py`: property-style — build randomised graphs (owner/collab/exclusion at app and track level, visibility settings, org/personal workspaces, guest membership), assert `resolve_roles_bulk` == `{id: await resolve_role(...)}` for every id. Run on Postgres in CI (`smoke` marker) with a fixed seed.
- `test_resolve_role_bench.py`: add a bulk case at 50 / 500 tracks; assert round trips ≤ 8 via `X-DB-Round-Trip-Count` or `db_op_counter`.
- `test_perf_query_budget.py`: `test_list_tracks_query_budget` budget drops from 40 to ≤ 15; multi-track entries list from 50 to ≤ 30. Record deltas in comments.

**Gate:** parity suite green on Postgres; Phase B `/tracks` cold for the 300-track persona within 2× of warm; process cache deleted; `make verify` + `make test-postgres` green.

---

## Phase D — Index alignment, search, GIN (D.1–D.3 parallel-safe; D.4 after jvspatial Phase 3)

**Files:** `models/nodes.py`, `services/pagination.py`, `services/entry_search.py`, `services/entry_listing.py`, `services/db_init.py`, `main.py` (`_ensure_model_indexes`), `docs/backend/content-profile-search-index.md`.

### D.1 Align Entry index with the listing sort

`DEFAULT_ENTITY_SORT = [("context.updated_at", -1), ("id", -1)]` but `Entry` declares `@compound_index([("track_id", 1), ("created_at", -1)])`. Add `@compound_index([("track_id", 1), ("updated_at", -1)], name="idx_entry_track_updated")` and keep the created one (views may sort by it). Verify with `EXPLAIN (ANALYZE)` on the Phase B hub track that page 50 no longer sorts 200k rows: the plan must be an index scan with `LIMIT`, no `Sort` node. Do the same for `author_id` (`idx_entry_author_created` → add `updated` variant) since the workspace-less path filters on `context.author_id`. Note the `id` tiebreak: on 0.0.18 with `entity`-leading indexes include `id` as the trailing key if jvspatial supports column mixing; otherwise accept the residual sort on ties.

### D.2 Per-class indexes for the other find() shapes

`models/nodes.py` already indexes most scalar lookups (`Track.workspace_id`, `App.workspace_id`/`app_id`, `Tag.track_id`/`app_id`/`parent_tag_id`, `EntryType.track_id`, `Entry.track_id`/`author_id`, `Invitation.workspace_id`/`email`/`target_resource_id`, `ShareLink.token_hash` unique-partial, `User.user_id`, `Notification.user_id`+`read`). From the audit's `find()` inventory the **unindexed** shapes are: `Invitation.token_hash` (~L364, redeem path — make it a unique partial index like `ShareLink.token_hash`), `Invitation.status` (compound with `email`), `App.lifecycle_state` (compound with `workspace_id`), `Track.attached_content_profile_id` and `App.attached_content_profile_id` (~L390/L479), `ChatThread.provider_session_id` (~L749), `ContentProfile.library_package` (~L177), `ShareLink.created_by`, and the comment/attachment relations: `Comment` has no `entry_id` scalar at all (threads resolve via `HAS_COMMENT` edges — fine on the indexed edge table; `services/entry_comment_stats.py::prefetch_comment_counts` is already one edge query per page, but it materialises every edge row to count in Python — switch it to a grouped `COUNT` once jvspatial exposes `count_connected_nodes`/grouped counts, or accept it since a page is ≤ 50 entries), and `UploadSession.entry_id` (~L616) + `status`. Each is one line on the model; `main.py::_ensure_model_indexes` materialises them at boot. Add a test that lists `pg_indexes` on `node` and asserts each expected name exists after boot (`tests/test_model_indexes_postgres.py`, Postgres-only), and — after jvspatial Phase 3 — that they are `entity`-leading.

### D.3 Search: escape now, `$text` on 0.0.18

- Immediately: `entry_search_query_clause` wraps the needle in `re.escape(...)` and caps length (e.g. 200 chars); add a test that `q="("` returns 200 not 500. Also apply to `Entry.find({"custom_fields.__seed_id": {"$regex": f"^{prefix}"}})` and any other `$regex` builder (grep `"\$regex"`).
- On 0.0.18: `Entry` gets `@fulltext_index(["title", "body"])`; `entry_search_query_clause` emits `{"$text": {"$search": needle, "$fields": ["context.title", "context.body"]}}`; the Python fallback `filter_entries_by_query` stays for non-PG backends. Update `docs/backend/content-profile-search-index.md` to describe the substrate path vs the hybrid-retrieval path (`services/retrieval/`) so nobody adds a third.

### D.4 GIN off

Grep `app/` for query operators that need the whole-document GIN (`$all`, `$elemMatch`, containment on arrays such as `tags`, `attachment_ids`). If any exist on hot paths, add a targeted GIN expression index on that array path via jvspatial's extended `create_index` instead. Then set `JVSPATIAL_PG_GIN_INDEX=off` in all envs and run Phase B; `REINDEX`/drop the old `node_data_gin` in the deploy step. Record the index-size delta.

**Gate:** `EXPLAIN` assertions in tests; Phase B `/entries` page 50 p95 within 1.2× page 1 on the hub track; search p95 < 100 ms local on 2M rows; `make test-postgres` green.

---

## Phase E — Resolve ADR-005 and make the API tier horizontally scalable

**Files:** `services/chat_turn_registry.py` (`_in_flight`), `agentive/services/agent_events.py` (`_agent_event_connections`), `main.py` boot warning, `deploy/docker-stack.*.yml`, `backend/.env.example`, `docs/backend/adr/005-*.md` (status → Superseded), new ADR-011.

Implement exactly what ADR-005's "What the real fix requires" lists, as **one unit of work**:

1. **Shared turn registry.** Redis is already in the stack for `JVAGENT_CONVERSATION_LOCK_REDIS_URL`; use it (`SET thread:<id> <worker_id> NX PX <ttl>` + heartbeat refresh) rather than adding a Postgres row type. If the team prefers Postgres, model it as an `Object` (I-GRAPH-02, like `StagedChangeRecord`) with an expiry column and lazy sweep — never a `Node`. Per-user concurrent-turn cap becomes a Redis counter or a `COUNT` over the shared registry.
2. **Websocket fan-out.** Redis pub/sub channel per user (`agent_events:<user_id>`); each worker subscribes for its connected users and re-emits locally. Postgres `LISTEN/NOTIFY` is the fallback if Redis is not wanted; do not ship both.
3. **Caches.** `JVSPATIAL_CACHE_BACKEND=redis` in stacks with `replicas > 1`; the permissions process cache is already gone after Phase C; audit `agentive/` and `services/` for any other module-level dict that is a cache or registry (`grep -n "^_[a-z_]* *: *Dict" app -r`) and either make it request-scoped, Redis-backed, or document it as safe-per-process.
4. **Boot guard flips.** The `WORKERS > 1` warning becomes: warn if `WORKERS > 1` **and** the turn registry backend is `memory`. Stack files: `WEB_CONCURRENCY: "2"` on main/prod with a comment on sizing, `replicas: 2` for `api`, Traefik sticky sessions for websockets (`traefik.http.services.<svc>.loadbalancer.sticky.cookie=true`).
5. **Tests.** Two-process integration test (spawn two uvicorn workers against one Redis + Postgres): same-thread double-turn rejected; event emitted on worker A reaches a socket on worker B; per-user cap enforced across workers.
6. ADR-011 "Shared turn state and fan-out" records the choice; ADR-005 marked Superseded by 011.

**Gate:** Phase B run with `WEB_CONCURRENCY=2, replicas=2`; throughput on `/entries` list ≥ 1.7× single-worker; no test regressions; chat integration test green.

---

## Phase F — Tenancy model (decision required; not blocking A–E)

`ARCHITECTURE.md` must stop claiming node-id partitioning and clustering; ROADMAP T1 needs a concrete design. Three options, all compatible with jvspatial 0.0.17+:

| Option | Mechanism | Isolation | Cost | Fit |
|---|---|---|---|---|
| F1 Shared table, no tenancy column | Today. Workspace scope enforced in `services/request_scope.py` only. | Application-level | None | SaaS free/turnkey tier at small scale |
| F2 Shared table + `tenant_id` + RLS | jvspatial `db.tenant(tenant_id)` sets `app.tenant_id` GUC per request; `enable_rls()` adds policies; `tenant_id` = **customer/organisation id** (not workspace id — cross-workspace shares and guest grants live inside one customer). Set from the resolved principal's organisation, not from `X-Integral-Scope`. | DB-enforced within one database | Medium: every request opens a transaction with `SET LOCAL`; personal workspaces need a customer id; migration back-fills `tenant_id` | SaaS turnkey/enterprise on shared infra |
| F3 Schema-per-customer | `PostgresDB(schema_name=…)` selected per request from the principal's organisation; one schema per customer, same database; or one database per customer for on-prem | Strong; trivially exportable/deletable per customer (T1 "deletion → backup → restore") | Medium-high: connection pool per schema or `SET search_path`; index bootstrap per schema; cross-customer features impossible by design (which is the point) | Enterprise / on-prem tier; the roadmap's "per-tenant DB partitioning option" |

Recommendation: **F2 for SaaS, F3 for on-prem/enterprise**, both behind one `TenancyResolver` in `services/request_scope.py`. Design doc first (`docs/superpowers/specs/2026-XX-tenancy-model.md`), then implement. Until decided, fix the two ARCHITECTURE.md sentences and add a "Tenancy: application-scoped today; DB-enforced isolation planned (T1)" line.

---

## Phase G — Transactional create + wire (parallel-safe, small)

I-GRAPH-01 says "in the same transaction / unit of work"; `services/entry_create.py` (~L120–145) does `Entry.create(...)` then `track.connect(...)` with a compensating `entry.delete()` on failure. jvspatial exposes `begin_transaction` / `commit_transaction` / `rollback_transaction` on `PostgresDB` (0.0.17 `postgres.py` ~L1680). Add a `services/graph_txn.py` helper `async with graph_transaction():` that uses them when the backend supports transactions and is a no-op context otherwise, and wrap the create+wire pairs that the `graph_contiguousness_check.sh` guard already enumerates (Entry, Comment, Attachment, Notification, Invitation, ShareLink, ContentProfile attach). Verify jvspatial's transaction object routes `Node.create` / `connect` through the held connection (if it does not — the audit saw `PostgresTransaction.save/get/delete/find` only — file that as a jvspatial follow-up and keep the compensating delete). Test: inject a failure between create and wire; assert no orphan row.

---

## Acceptance summary

| # | Criterion | Evidence |
|---|---|---|
| A1 | jvspatial ≥0.0.19 pinned (no `JVSPATIAL_NODE_EDGE_IDS`), `strip_node_edges` in deploy scripts, `count_nodes`/`nodes_page` adopted, `len(nodes())` guard live | Phase A + 0.0.19 follow-up diff + `make verify` |
| B1 | `tests/load/` seeds 2M entries with a 200k hub; `docs/bench/2026-09-substrate-baseline.md` has before/after tables per phase | files in repo |
| C1 | `resolve_roles_bulk` parity suite green; `/tracks` cold ≤ 8 round trips at 500 tracks; process cache deleted | tests + bench |
| D1 | Entry `(track_id, updated_at DESC)` index; page-50 p95 ≤ 1.2× page-1 on hub track; per-class indexes asserted in `pg_indexes` | `EXPLAIN` tests + bench |
| D2 | Search escaped; `$text` + fulltext index on 0.0.18; `q="("` returns 200 | tests |
| D3 | GIN off with no regression in bench | bench + env |
| E1 | Two-worker/two-replica stack passes chat integration test; ADR-011 written; ADR-005 superseded | tests + docs |
| F1 | Tenancy decision recorded; ARCHITECTURE.md corrected | docs |
| G1 | Create+wire transactional where the backend supports it; orphan-injection test | tests |

## Ordering and parallelism

```
B (harness)  ──────────────┐
C (permissions)  ──────────┼──▶ A (adopt 0.0.18) ──▶ D.3b/D.4 ──▶ E ──▶ F
D.1–D.3a (indexes, escape) ┘                                   G anywhere
```

B, C, D.1–D.3a and G do not wait for jvspatial. A waits for jvspatial 0.0.18. E waits for C (so the process cache is gone) and A (so the entity cache backend switch is the only remaining cache). F is a design decision with a doc fix that can land any time.
