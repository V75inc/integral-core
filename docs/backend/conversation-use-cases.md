# Conversation Use Cases (CUCS) in Integral

Integral's resident agent runs on jvagent. Its conversational behavior is
documented and regression-tested with the **Conversation Use Case Specification
(CUCS)** — jvagent's portable YAML format (`schema: jvagent.use-case/v1`) for
multi-turn scenarios that drive deterministic orchestrator E2E tests.

A CUCS scenario captures one user journey: the world state before the
conversation (`given`), the ordered user turns (`turns`, each with the
utterance and the expected outcome), and an optional terminal `outcome`. It is
**not** Gherkin — the `then` blocks assert jvagent orchestrator primitives
(task graph, session, published reply, tool surface) plus Integral's own
extensions.

Normative framework spec, schema, and rationale live in jvagent:
`.planning/reference/conversation-use-cases.md`,
`jvagent/schemas/use-case-v1.schema.json`, `ADR-0027`. This document records how
Integral adopts CUCS, the namespaces Integral adds, and where scenarios live.

---

## Where scenarios live

CUCS is per-app: each app root owns a `use-cases/` tree.

| Layer | Scenario root | Companion index |
|---|---|---|
| **Core Integral** (base `integral_*` skills) | `agent/agents/integral/integral_agent/use-cases/<domain>/*.yaml` | this document, §Index |
| **App bundle** (skills installed with a content profile) | `backend/app/profiles/<slug>/use-cases/*.yaml` | the bundle's `profile.yaml` references; scenarios self-document |

Rules (from the framework spec):

- **One file per scenario.** The stable dot-separated `id` matches the filename stem where practical (`scaffold.app.one-batch` → `app-one-batch.yaml`).
- Reusable fixture stubs live under `<root>/use-cases/stubs/` and are **excluded** from scenario discovery by the loader.
- Never duplicate a skill's `interview:` field graph — link it via `traceability.skills`.
- A bundle's scenarios use that bundle's overlay skill names (`{app_slug}__{skill_key}`, e.g. `crm__lead_intake`); core scenarios use the base `integral_*` skill names.

The loader (`jvagent/testing/use_case_loader.py`) requires `schema`,
`id`, `title`, `given`, and a non-empty `turns`. Author to the full JSON
Schema (exact `id`/turn-id patterns, `additionalProperties: false` on `given`/
`turn`/`when`/the framework `then` namespaces) so scenarios survive strict
validation.

---

## Assertion namespaces

### Framework-owned (jvagent) — do not redefine

- **`task_graph`** — `pushed` / `not_pushed` (prerequisite skill), `blocked` (`{parent: blocker}`), `seed` (`{skill: {utterance, fields}}`), `runnable` (top runnable SKILL task).
- **`context`** — dot-path expectations merged into `conversation.context`.
- **`session`** — the active interview session (`interview_type`, `status`, `fields`, `context`).
- **`publish`** — the user-visible reply: `contains: [...]`, `matches: <regex>`.
- **`tools_surface`** — tools exposed on a given `_run_model` call: `call_index`, `includes: [...]`.

### Integral extensions

Integral's resident agent stages every write as a `StagedChange` the user
blesses (never an immediate mutation), so Integral scenarios assert against the
**staging envelope** and, after a bless, against the **graph**. These live as
top-level `then` keys (the framework `assertions` object is open by extension)
and are evaluated by Integral's test adapter.

- **`staging`** — assert the staged change a turn mints, without blessing it:

  ```yaml
  then:
    staging:
      kind: batch                       # the StagedChange.kind
      ops: [create_app, create_track, create_track]   # ordered op kinds (batch)
      summary_contains: "QA Smoke CRM"  # substring of the human card summary
      no_raw_ids: true                  # summary/diff_human carry names, not n.<Type>.<hex> (I-CHAT-01)
  ```

- **`graph`** — assert what materialized after a blessed write (use only on a
  turn whose `harness` blesses, or in `outcome`):

  ```yaml
  then:
    graph:
      created:
        - { type: App, name: "QA Smoke CRM" }
        - { type: Track, title: "Contacts", in_app: "QA Smoke CRM" }
      edges:
        - { from_type: App, to_type: Track, edge: CONTAINS }
  ```

Both namespaces are workspace-scoped: assertions resolve against the workspace
named in `given.context.workspace` (or the personal workspace by default).

---

## `given` world state

```yaml
given:
  channel: web                  # web | whatsapp | default
  new_user: false
  context:
    workspace: acme             # the active workspace the turn runs in (Integral extension)
  preconditions:
    app_installed.crm: true     # named precondition evaluator (Integral-registered)
  fixtures:
    seed: workspace/acme-with-crm   # reusable stub under use-cases/stubs/
```

- `context.workspace` selects the active workspace (mirrors `X-Integral-Scope`).
  Bundle scenarios set it to a workspace that has the bundle's app installed —
  bundle skills surface **only** there (I-SKILL-SCOPE-01).
- `preconditions.app_installed.<slug>` gates bundle scenarios on the app being
  installed in that workspace.
- `fixtures.seed` references a reusable workspace stub.

---

## `harness.decisions` — deterministic model returns

Integral's orchestrator chooses skills and tools. A turn's `harness.decisions`
canned-return those choices so the E2E run is deterministic. Each decision is a
tool call or a final answer:

```yaml
harness:
  decisions:
    - action: tool
      tool: integral_begin_batch          # an integral_* tool the model invokes
    - action: tool
      tool: integral_create_app
      args: { name: "QA Smoke CRM" }
    - action: final
      answer: "Staged a new app 'QA Smoke CRM'. Approve the card to create it."
```

`harness` is optional; supply it for any turn whose `then` asserts model-driven
behavior (a staged change, published text, tool surface).

---

## Index

### Core (base skills) — `agent/agents/integral/integral_agent/use-cases/`

| ID | File | Demonstrates |
|----|------|--------------|
| `identity.orientation.workspace` | `identity/workspace-orientation.yaml` | "what workspace am I in" → names resolved, no raw ids (I-CHAT-01) |
| `scaffold.app.one-batch` | `scaffold/app-one-batch.yaml` | "set up a CRM" → `integral_scaffold` → one batch card (app + tracks) |
| `model.relation.cross-track` | `model/relation-cross-track.yaml` | add a cross-track lookup relation → `integral_model` (nested `relation`, `allow_cross_track`) |
| `organize.bulk.tag` | `organize/bulk-tag.yaml` | tag N entries in one batch → `integral_organize` (create_tag + add_entry_tag, `{{tag.id}}` ref) |

### App bundles — `backend/app/profiles/<slug>/use-cases/`

| ID | File | Bundle | Demonstrates |
|----|------|--------|--------------|
| `crm.lead.intake` | `crm/use-cases/lead-intake.yaml` | crm | freeform lead → file as `Lead` in Contacts |
| `crm.opportunity.handoff` | `crm/use-cases/opportunity-handoff.yaml` | crm | mark opportunity `won` → spawn Projects delivery entry |
| `hr.timeoff.process` | `hr_app/use-cases/process-time-off.yaml` | hr_app | create + approve a time-off request → `leave_balance` hook recalculates the employee |

---

## Running

CUCS scenarios feed orchestrator E2E suites under `backend/tests/`. The loader
discovers every `*.yaml` under a `use-cases/` root (excluding `stubs/`),
validates the schema, and the Integral adapter evaluates the `staging` / `graph`
namespaces against an in-memory workspace. Scenarios are documentation first and
test fixtures second — a reviewer reads `when`/`then` without needing `harness`.
