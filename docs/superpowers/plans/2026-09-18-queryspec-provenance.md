# Bounded QuerySpec and Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a broker-governed, workspace-safe QuerySpec for Core Entry, Track, and App resources with durable result provenance.

**Architecture:** A typed QuerySpec compiler validates fields, filters, sorting, projection, traversal, row limits, and cost before graph access. The capability broker remains the only execution entry; a record-shaped `QueryResultSet` stores plan/provenance metadata while `RunStep` stores fingerprints only. App manifests may declare fixed query capabilities but cannot pass open Core QuerySpecs.

**Tech Stack:** Python, Pydantic, jvspatial Object/Node APIs, FastAPI-compatible `@endpoint`, pytest.

---

### Task 1: Typed QuerySpec contract

**Files:**
- Create: `backend/app/schemas/query_spec.py`
- Create: `backend/tests/test_query_spec.py`

- [ ] **Step 1: Write failing schema tests**

Cover:

```python
QuerySpec(
    resource="entry",
    select=["id", "title", "status"],
    filters=[{"field": "status", "op": "eq", "value": "open"}],
    sort=[{"field": "updated_at", "direction": "desc"}],
    limit=20,
    cost_ceiling=100,
)
```

Assert unknown resources/operators, more than eight filters, more than two sort keys, limits above 100, and traversal depth above one fail validation.

- [ ] **Step 2: Run RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short tests/test_query_spec.py
```

Expected: import failure for `app.schemas.query_spec`.

- [ ] **Step 3: Implement schemas**

Define `QueryFilter`, `QuerySort`, `QueryTraversal`, `QuerySpec`, `QueryItemProvenance`, and `QuerySpecResult`. Every model uses `extra="forbid"`; list and numeric bounds live at the Pydantic boundary.

- [ ] **Step 4: Run GREEN**

Run the Task 1 command. Expected: all schema tests pass.

### Task 2: Compiler, authorization, cost, and pagination

**Files:**
- Create: `backend/app/agentive/services/query_spec.py`
- Modify: `backend/tests/test_query_spec.py`

- [ ] **Step 1: Write failing compiler tests**

Create two users and two workspaces. Assert:

```python
result = await execute_query_spec(
    principal_id=user_a.id,
    workspace_id=workspace_a.id,
    spec=QuerySpec(resource="entry", select=["id", "title"], limit=20),
)
assert {row["id"] for row in result.items} == {entry_a.id}
assert entry_b.id not in result.model_dump_json()
```

Also assert field/edge allowlists reject before `Entry.find`, cost ceilings reject predictably, cursors cannot be reused with another plan, and traversed rows are independently permission-filtered.

- [ ] **Step 2: Run RED**

Expected: import failure for `execute_query_spec`.

- [ ] **Step 3: Implement compiler and executor**

Use explicit resource field maps and these edges:

```python
ENTRY_EDGES = {"track", "references", "anchored_tracks"}
TRACK_EDGES = {"app", "entries"}
APP_EDGES = {"tracks"}
```

Resolve root candidates only through `get_user_accessible_entries`, `get_user_accessible_tracks`, and `get_user_accessible_apps`, narrowed to `workspace_id` before filtering, counting, sorting, or pagination. Support one-hop traversal only. Encode cursors as opaque plan-fingerprint plus offset. Compute cost before graph access from limit, selected fields, filters, sort keys, and traversals.

- [ ] **Step 4: Run GREEN**

Run the Task 1 command. Expected: compiler and adversarial scope tests pass.

### Task 3: Durable result-set provenance

**Files:**
- Create: `backend/app/models/query_result_set.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/agentive/services/query_spec.py`
- Modify: `backend/tests/test_query_spec.py`

- [ ] **Step 1: Write failing provenance tests**

Assert every result has:

```python
{
    "result_set_id": str,
    "normalized_plan": dict,
    "graph_revision": str,
    "item_provenance": list,
    "redaction_state": "none" | "fields_redacted",
}
```

Assert `QueryResultSet` stores IDs, normalized plan, item fingerprints/provenance, run identity, and idempotency identity—but no entry body, title, or custom-field value.

- [ ] **Step 2: Run RED**

Expected: missing `QueryResultSet`.

- [ ] **Step 3: Implement durable metadata**

Model `QueryResultSet` as `jvspatial.core.Object` per I-GRAPH-02. Generate graph revision from sorted authorized item IDs plus their `updated_at` values. Store only metadata and item fingerprints; live response rows are not copied into the durable record.

- [ ] **Step 4: Run GREEN**

Run the Task 1 command. Expected: provenance and privacy tests pass.

### Task 4: Broker, HTTP, resident, and App declarations

**Files:**
- Modify: `backend/app/agentive/services/capability_adapters.py`
- Modify: `backend/app/agentive/tool_manifest.yaml`
- Modify: `backend/app/agentive/tooling/bindings.py`
- Create: `backend/app/api/query_spec.py`
- Modify: `backend/app/api/__init__.py`
- Modify: `backend/app/services/content_profile_compile.py`
- Modify: `backend/app/agentive/services/execution_runs.py`
- Modify: `agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/integral_insights/SKILL.md`
- Modify: `backend/tests/test_query_spec.py`
- Modify: `backend/tests/test_mcp_server_dispatch.py`
- Modify: `backend/tests/test_mcp_server_oauth.py`

- [ ] **Step 1: Write failing surface tests**

Assert `integral_query_spec` is a read capability in snapshots and resident/MCP catalogues; `POST /api/query-spec` mints/reuses a run and returns the same broker envelope semantics; result metadata links to its receipt. Assert App `queries[]` compilation accepts fixed declared descriptors and rejects descriptors containing caller-supplied `resource`, `filters`, `traversal`, or `select`.

- [ ] **Step 2: Run RED**

Expected: missing capability, endpoint, and manifest query parser.

- [ ] **Step 3: Wire broker-owned execution**

Special-case the Core QuerySpec adapter so it receives `run_id`, principal, workspace, and broker-derived idempotency identity. Add the tool declaration/binding and resident insight skill exposure. Add the authenticated `@endpoint` that invokes `invoke_declared_capability`; it must not call the compiler directly.

Compile App `queries[]` into fixed descriptors with a declared `key`, `handler_key`, input schema, output schema, and bounded fixed query template. Do not register an open `QuerySpec` parameter for App sources.

- [ ] **Step 4: Run GREEN**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q --tb=short \
  tests/test_query_spec.py tests/test_capability_broker.py \
  tests/test_mcp_server_dispatch.py tests/test_mcp_server_oauth.py
```

Expected: all tests pass.

### Task 5: Claim/debug linkage and release gate

**Files:**
- Modify: `backend/app/providers/jvagent_streaming.py`
- Modify: `frontend/src/features/ai-chat/components/MessageDebugDialog.tsx`
- Modify: `backend/tests/test_query_spec.py`

- [ ] **Step 1: Write failing debug-envelope test**

Assert a QuerySpec-backed tool result contributes `result_set_id`, `run_id`, and receipt reference to debug metadata, while page-context payloads never receive a result-set label.

- [ ] **Step 2: Implement debug metadata**

Propagate only identifiers and provenance metadata. Never copy result rows into debug receipts.

- [ ] **Step 3: Run focused tests**

Run backend QuerySpec/broker tests plus frontend typecheck and relevant component tests.

- [ ] **Step 4: Run complete gate**

Run:

```bash
make verify
```

Expected: every guard, formatter, typecheck, CI-faithful smoke run, frontend suite, and backend suite passes.
