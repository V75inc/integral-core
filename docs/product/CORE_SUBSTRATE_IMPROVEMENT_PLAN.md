# Integral Core substrate capability improvement plan

**Snapshot:** 2026-09-26. **Goal:** bring the Core skills, tools, and supporting facilities to a complete and reliable form for every use case in [CORE_SUBSTRATE_USE_CASES.md](CORE_SUBSTRATE_USE_CASES.md), with the flagship App-design experience first. This is a proposed implementation and qualification program; [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md) remains the release record and this plan does not reclassify any C-row.

## 1. Baseline verdict

The foundation is implemented: graph primitives, Operational Model compilation and publishing, governed tools, staging, approved-design builds, retrieval, skill overlays, routines, and MCP. Implementation is not blanket journey proof. The [use-case inventory](CORE_SUBSTRATE_USE_CASES.md) distinguishes **Implemented**, **Tested journey**, **Composed**, **Partial**, and **Gap**, with evidence attached to individual claims. C5 remains a platform contract; model-led delivery is qualified separately in the external exam.

| Pillar | Current coverage | Main shortfall |
| --- | --- | --- |
| Flagship App design | Implemented primitives; selected Tested journeys; delivery judgment Composed | Tags and anchor templates missing from build; custom operations require developer handoff; verification is prompt-only. |
| Query and inference | Implemented list/filter/count/search; end-to-end reasoning Partial | No field aggregates, no business-field group-by, one hop only; packaged-App boundary differs across surfaces. |
| Intelligent filing | Implemented mechanical stager; routing Composed | No destination ranking or no-fit branch; create-only; relations/tags not first-class. |
| Substrate evolution | Implemented draft/diff/publish; conversational editing Partial | Rename/type-change do not emit migrations; no move/merge/split; tag rename/reparent lacks agent binding. |
| Dashboards | Implemented storage/CRUD; inference Partial | Count-only data sources; suggester ignores field types. |
| Reliability | Selected Tested journeys; broader qualification Partial | Extend existing deterministic suites and external live exam; correct skill drift. |

## 2. Gap register

Each gap carries an ID used by the work packages in §4.

### Flagship App design

| ID | Gap | Evidence | User consequence |
| --- | --- | --- | --- |
| G01 | Tags/taxonomy cannot be part of an approved build. | `_PLAN_TOOLS` omits `integral_create_tag` ([`scaffold_build.py:24-34`](../../backend/app/agentive/tooling/scaffold_build.py)); inline track compile has no `taxonomy.tag_groups` (`validate_inline_entry_types` in `operational_model_authoring.py`). | Apps ship without a classification vocabulary; the designed tags become a follow-up chore. |
| G02 | Anchored detail Tracks (`ANCHORS`) are not compiled by the plan builder. | Builder annotates entry-target lookups only. | "Each project has its own tasks and notes" requires a second, manual schema revision. |
| G03 | No custom-tool/operation path from the resident. | `author_skill` produces a declarative, untrusted `Skill`; manifest `skill_governance.capability_growth_path` requires a trusted package. | Requirements needing a transaction or external API are silently downgraded to prose SOPs. |
| G04 | Verification is an SOP, not a tool. | Skill §4; WP-06 evidence records false "verified" claims. | "Built" can be reported for a partial App. |
| G05 | Authorized App-private skills are hard to discover outside App focus. The private default is an intentional access boundary, not the defect. | `workspace_agent_profile.py` excludes `private=True` App skills unless that App is focused. | A user who may use a build-authored skill gets no hint it exists when speaking from elsewhere; the design never asks whether a skill should be workspace-wide. |
| G06 | No export of a built App as a reusable library package (gated expansion, §4 D1). | `export_app` → `app_export.py` is a data dump. | Good designs cannot be reused across workspaces. |
| G07 | Dashboard in the build inherits the shallow suggester. | See G19–G20. | New Apps get generic boards. |
| G08 | Design blueprint has no machine schema. | `propose_design` stores markdown plus loose markers; `plan_differs_from_design` string-matches names. | Design fidelity checks are brittle; amendments can drop requirements. |

### Query and inference

| ID | Gap | Evidence | User consequence |
| --- | --- | --- | --- |
| G09 | No aggregate-by-field (sum/avg/min/max). | `count_entries_grouped` only counts (`agent_insights.py`). | "Total pipeline value" is unanswerable or computed by the model over a truncated page. |
| G10 | `count_entries` cannot group by business fields. | Enum `track/status/tag/entry_type` (+ undocumented `date`). | "Deals by stage" fails through the query path. |
| G11 | `get_related` walks inbound `REFERENCES` only. | [`entry_relations.py:128`](../../backend/app/api/entry_relations.py). | Outbound links and anchored detail are invisible to "what is this connected to?". |
| G12 | Traversal is one hop (gated expansion, §4 D2). | QuerySpec `depth: Literal[1]`; governed open scans do not traverse. | No multi-hop inference across Apps. |
| G13 | No query planner. | WP-08 in [HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md](HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md) not shipped. | Tool choice (semantic vs exact vs count) depends on SOP adherence. |
| G14 | Query paths scan in memory. | `query_entries`/counts hydrate accessible entries then filter. | Latency and truncation grow with workspace size. |
| G15 | `query_entries` sorts only by platform keys; end-to-end ranking ignores the primitive that already exists; provenance not chainable. | `query_entries` `sort_by` ∈ `updated_at/created_at/title`, while QuerySpec already accepts qualified `custom_fields.*` sort keys (`schemas/query_spec.py`); the insights skill never routes superlatives to it; `result_set_id` accepted by no tool. | Rankings by business value fall back to fetch-and-reason over a capped page; follow-up questions re-query from scratch. |

### Intelligent filing

| ID | Gap | Evidence | User consequence |
| --- | --- | --- | --- |
| G16 | No destination ranking contract. | [`filing_resolution.py`](../../backend/app/services/filing_resolution.py) docstring: mechanical only; `filing_candidates` is a refusal payload. | Destination choice is opaque and unreviewable. |
| G17 | No no-fit branch. | Unresolved message says "call list_tracks and supply hints". | Novel content is forced into a poor Track or dropped. |
| G18 | Filing is create-only; relations absent; tags supported but unadvertised; dedupe is SOP-only. | `_x_file_content` → `_x_create_entry`; manifest params omit `tags`. | Duplicates, orphan records, and a second round of link/tag cards. |

### Dashboards

| ID | Gap | Evidence | User consequence |
| --- | --- | --- | --- |
| G19 | Data sources are counts only. | `DataSourceSpec` kinds `count`/`grouped_count`/digests. | No money, duration, or quantity KPIs. |
| G20 | Suggester ignores the schema. | `suggest_dashboard_template` keys on Track count and platform `status`. | "Best dashboard for this App" is generic. |

### Substrate evolution

| ID | Gap | Evidence | User consequence |
| --- | --- | --- | --- |
| G21 | Rename field / change type do not emit migrations. | Patch DSL has no `rename_field`; `modify_field` can change `key`/`type`; migrations (`rename_field`, `coerce_type`) must be hand-authored ([`agent_profile_patches.py:336-351`](../../backend/app/services/agent_profile_patches.py), [`operational_model_migrations.py`](../../backend/app/services/operational_model_migrations.py)). | Risk of orphaned values on a routine rename. |
| G22 | `integral_modify_model` cannot edit fields. | Actions are add/remove entry type, view, tag only. | Skills that point field edits at it fail. |
| G23 | No bulk move between Tracks. | Manifest `gap`. | Misfiled records cannot be corrected in place. |
| G24 | No Track merge/split, tag merge, or field reorder in the substrate; tag rename/reparent exists in Core but has no agent binding. | Core `PUT /tags/{tag_id}` accepts `name` and `parent_tag_id` (`api/tags.py`); no manifest tool binds it; merge/split/reorder absent from patch DSL and tools. | Configuration cannot mature with use. |

### Reliability and experience

| ID | Gap | Evidence | User consequence |
| --- | --- | --- | --- |
| G25 | Skill text contradicts code (see §3). | Multiple. | The model follows wrong instructions. |
| G26 | Per-pillar qualification coverage and scheduled evidence are incomplete. | Existing WP-06 profile, runner, compiler, and evaluator provide the foundation; domain exam material remains external under C5. | Regressions need systematic deterministic assertions and external live scorecards. |
| G27 | Open, uncommitted batches are process-local. Pending and blessed staged changes are already durable. | `_open_batches` dict in `staging.py`; `staging_store` write-through persistence covered by `test_staging_persistence.py` / `test_staging_resume.py`. | A restart mid-build loses the batch being assembled (approved-design builds commit in one call, so exposure is chiefly manual/recovery batches). |
| G28 | No turn-specific view of loaded skills and effective capabilities; Mission Control lacks pending approvals/inbox integration. Workspace skill management already exists. | [`SkillsSection.tsx`](../../frontend/src/features/settings/sections/SkillsSection.tsx) lists core, App, and workspace skills; [`MissionControlPage.tsx`](../../frontend/src/pages/MissionControlPage.tsx) has no pending-approval panel. | Users can manage skills in Settings but cannot inspect which are available to this turn or why one is excluded. |
| G29 | Packaged-App query restrictions differ across existing read paths. | `governed_query/engine.py` excludes App-domain Tracks from open Entry scans; `agentive/services/query_spec.py` and `services/agent_insights.py` use permission-filtered Entry reads without that same package-class exclusion. | A restriction advertised for the governed query path cannot yet be assumed for every generic read tool. |
| G30 | Saved-view execution silently drops filters it cannot translate. Found in W0.1. | `entry_listing._view_filter_to_clause` returns `None` for `in`/`not_in`, and the listing reads only the `operator` key, so a view saved with `op` is treated as `eq`; `normalize_view_config` validates only that `filters` is a list. | A saved view can show more (or different) records than the user approved, with no error. |
| G31 | `query_entries` advertises map-only `filters`; range predicates on model-defined fields are reachable only through `integral_query_spec`. Found in W0.1. | Manifest `filters: {type: object}` while `agent_insights.query_entries` accepts the list form (`normalize_filter_expressions`). | The model cannot express "due before Friday" on the most-used query tool. |
| G32 | Custom-field keys are not resolved or refused on either side. Found in the W0 browser smoke. | Write: `integral_create_entry` passes `{"Value": …, "Stage": …}` through unnormalized; the API rejects `Value`, and the non-strict executor (`staging_executors._x_create_entry`) drops the field, retries, and the receipt still reads "Updates applied". Read: QuerySpec accepts `custom_fields.Value` in `select`/`sort`, returns `null` for every row, and orders arbitrarily. | An approved record is stored without the values on its card; a ranking over an unknown key looks like an answer. |

## 3. Skill ↔ implementation drift — fix immediately

Status reflects the W0.1 correction cohort. Assertions named `dNN` are `test_w01_dNN_*` in `backend/tests/test_resident_skill_runtime_alignment.py`; other suites are named in full. Rows marked *found in W0.1* were discovered while tracing the original rows.

| ID | Location | Said | Code truth | Fix | Status | Assertion |
| --- | --- | --- | --- | --- | --- | --- |
| d01 | `integral_scaffold` view palette and weave | "Every track should get [a table]"; "table ↔ always". | Builder adds a table only when the approved design names one ([`scaffold_build.py` `_expand_track`](../../backend/app/agentive/tooling/scaffold_build.py)). | Table only when the design names it. | Fixed | d01_d02; `test_scaffold_build_plan::test_omitted_views_do_not_invent_a_table_or_calendar` |
| d02 | `integral_scaffold` build section; manifest `integral_build_approved_design` (*manifest found in W0.1*) | Builder "fills omitted baseline tables and date calendars … plus synthetic demo records". | It adds a table or dashboard only when the design asks and never invents records; named seeds must be in the plan. | State the actual behavior in both places. | Fixed | d01_d02; `test_scaffold_build_plan::test_named_seed_blocks_invented_example_rows` |
| d03 | `integral_scaffold` field palette | Implied tags are part of the build. | Not in `_PLAN_TOOLS`. | Interim: tags are a post-build step (scaffold may call `integral_create_tag`). W1.1 replaces this wording when build support ships. | Fixed (W1.1: tag groups build inline on `integral_create_app_track.taxonomy`; `integral_create_tag` is a plan tool) | d03 |
| d04 | `integral_insights` superlative flow and example | `query_entries` rows are summaries without `custom_fields`; resolve each. | Rows include `custom_fields` (`agent_insights.py`). QuerySpec sorts every readable match on `custom_fields.*` before paging. | Rank custom fields with a sorted `integral_query_spec`; read row maps only when the page is complete. | Fixed | d04 (two tests); `test_agent_insights_workspace_scope::test_w01_rows_carry_custom_fields_and_tag_filters_match_ids` |
| d05 | Saved-view filters: `integral_insights` example | `config={filters: {status: "open", …}}`. | Traced: `modify_operational_model(add_view)` → `normalize_view_config` rejects a map (`view.config.filters must be a list`) at execution, after approval; `entry_listing` executes a list of `{field, operator, value}` and has no `in`. | Document the list shape and operator set; example uses `entry_type_keys` for type narrowing. Silent-drop behavior recorded as G30. | Fixed | d05 |
| d06 | `integral_insights` §Execute | `query`/`query_entries` cannot filter by date. | `since`/`until` on `query_entries` and counts bound the update window. `custom_fields.<date>` ranges need list-form `gte`/`lte`, which only `integral_query_spec` advertises (it does not resolve `relative_date_days`). | Document both paths; the `query_entries` schema gap is G31. | Fixed | d06 |
| d07 | `integral_insights` Forbidden | Calls `get_feed`/`list_notifications` gap tools. | Both are live and allowed in its frontmatter. | Delete the line. | Fixed | d07 (generic: no core skill calls a live tool a gap tool) |
| d08 | Manifest `integral_get_related` | Walks REFERENCES/ANCHORS. | Inbound REFERENCES only. | Correct now; widen in W3.3. | Fixed | d08 |
| d09 | Manifest `integral_count_entries` | `group_by` enum omits `date`. | `date` (creation day) implemented. | Add to enum; enum pinned to the service `GroupBy`. | Fixed | d09 |
| d10 | Manifest `integral_file_content` | No `tags` param. | Stager and executor honor `tags`. | Advertise it (as tag ids, see d15). | Fixed | d10 |
| d11 | Manifest attachments domain | "Association via file_content staging". | Separate attach tools. | Name the attach tools. | Fixed | d11 |
| d12 | `integral_model` / `integral_models` | `modify_model` suggested for a field addition. | `modify_model` is entry-type/view/tag level; `modify_field` changes `key`/`type` without migrating values (G21). | Route field edits to the draft lifecycle and warn about key/type changes. | Fixed | d12 |
| d13 | [BYOA.md](BYOA.md) | `integral_propose_*`, `list_spaces`, `list_entries`, `get_entry`, `integral_execute_<token>`, stale counts. | MCP advertises manifest names verbatim; scope selects op classes. | Rewrite the catalogue section and walkthrough. | Fixed | d13 (every named tool and call example validated) |
| d14 | `staging.py` module docstring | "In-memory dict … restart drops pending tokens." | Pending/blessed tokens persist via `staging_store`; open batches are process-local. | Update docstring. | Fixed | d14 |
| d15 | Manifest `query_entries`/`count_entries` `tags` (*found in W0.1*) | Filter by tag **names**. | Entries store tag ids; the filter compares ids, so names never match; `group_by=tag` returns ids. | Advertise ids; insights maps ids to names with `integral_list_tags`. | Fixed | d10; `test_agent_insights_workspace_scope::test_w01_rows_carry_custom_fields_and_tag_filters_match_ids` |
| d16 | `integral_model` example (*found in W0.1*) | `integral_link_entries(source=…)`. | Parameter is `source_entry_id`. | Correct the example. | Fixed | `test_skill_compliance::test_all_skills_compliance` (call-example check) |
| d17 | `integral_insights` custom-field ranking (*found in the W0 browser smoke*) | `custom_fields.<key>` with no instruction on where the key comes from. | The resident sorted on `custom_fields.Value` (the label; the key is `value`). QuerySpec returned `null` for every row, the model supplied a value from memory, and it reported a wrong second place. | Read keys from `integral_get_track_schema`; an all-null ranking field means a wrong key, so re-read and re-run and never fill values from memory. Substrate half is G32. | Fixed | d17 |

W0.2 turns this table into a CI check so drift cannot reaccumulate: every call-form example in a core or public App skill (and in BYOA.md) is validated against the advertised tool schema — tool name, argument names, enum literals, and keys of closed object parameters.

## 4. Work packages

Wave numbers group related work; they do not impose a serial schedule. The explicit `depends_on` and ownership table in §8 is authoritative. Independent packages may run concurrently after their prerequisite contracts are frozen. Every package ships with deterministic contract tests and mapped qualification assertions (W0.3a); model-led scenarios run externally under W0.3b. New Core surfaces follow the jvspatial contract: `@endpoint`, schemas in `app/schemas/`, walkers for multi-hop, structural edges at every create, no domain references in Core.

### Wave 0 — deterministic prerequisites and external qualification

| Package | Work | Exit evidence |
| --- | --- | --- |
| **W0.1 Drift fixes** | Apply the current-contract corrections in §3. The field-palette row has interim wording now and final wording owned by W1.1. Confirm saved-view filter shape through execution before correcting examples. | Every current drift row maps to a passing regression assertion in the existing W0.2 suites; include assertion IDs in the evidence record. |
| **W0.2 Skill-contract assertions** | Extend `backend/tests/test_skill_compliance.py` (already invoked by `.ci/skill_compliance_check.sh` and checking tool lists), `test_resident_skill_runtime_alignment.py` (skill-text truths), and `test_tool_manifest_reconciliation.py` (bindings). Add only missing checks: tool names in prose/examples exist; example arguments validate against manifest schemas; builder-default claims are pinned by fixtures. Keep the existing check entry points; no parallel checker. | Each §3 correction has a regression assertion; obsolete prose tools, invalid example arguments, and false default claims fail the existing suites in CI. |
| **W0.3a Deterministic qualification contract** | Reuse the [WP-06 profile](evidence/wp-06-live-model-qualification.yaml), `scripts/evaluate_live_model_qualification.py`, `scripts/compile_live_model_qualification.py`, and existing `test_live_model_qualification_runner.py` / `test_live_model_qualification_compiler.py` suites. Freeze fixture format, independent expected outcomes, scorer, and development/held-out split. Core retains generic synthetic scorer fixtures and profile metadata; domain prompts and expected records live in an external exam repository/artifact under Q custody, per C5. Record its immutable version/digest and access-controlled locator; do not copy domain cases into Core. | Deterministic scorer/compiler/runner fixtures run in CI without model credentials. Q records the external corpus location, split manifest, and digest. Skill authors can see development cases but cannot access held-out prompts or expected records; exam reports expose aggregate results and redacted failures without disclosing held-out content. **Delivered:** validator `scripts/validate_qualification_contract.py` (evidence template + split manifest; `--require-recorded` is the W0.3b refusal gate), [`evidence/wp-06-split-manifest.yaml`](evidence/wp-06-split-manifest.yaml) (ids and splits only; `pending_q_custody` until Q records locator, version, and digests), synthetic scorer fixtures with hand-authored expected reports under `backend/tests/fixtures/qualification/`, Wave 0 records under [`evidence/packages/`](evidence/packages/), and `tests/test_qualification_contract.py` plus the runner/compiler suites in the CI smoke set. |
| **W0.3b Live qualification and baseline** | Extend the existing WP-06 lane (`scripts/run_qualification_lane.py`), compiler, and evaluator; do not build a second harness. Run external pillar scenarios nightly: App design across ≥6 held-out domains; filing decoys/duplicates/no-fit; queries with dates, aggregates, and multiple pages; populated-data evolution; dashboards. Keep future-feature cases explicitly unsupported until delivery, and multi-hop inactive until D2. Freeze provider/model ID and observed version, harness binding, configuration digest, candidate SHA, repetitions, and corpus digest for each comparable baseline. Q and the pod leads set a numeric per-run token/cost ceiling and nightly currency budget before enabling runs; use scoped secret-store credentials with named custody, never credentials in evidence. Missing pins, credentials, or budget refuse the live run without blocking B0. | Baseline scorecard and nightly evidence use the WP-06 profile's repetitions, success rate, safety assertions, latency/token budgets, and redaction rules. Record fidelity, false success, duplicate effect, scope violations, interventions, tokens, latency, and billed cost. Stop scheduling when the approved budget is exhausted; record incomplete qualification, never a pass. W0.3b gates later completion as defined in §8, not implementation starts. |
| **W0.4 Generated capability map** | Extend WP-05's compiled catalogue using `tooling/catalogue.py:build_tool_catalogue`, `agentive/services/agent_skills.py:build_tool_catalogue_for_editor`, and `describe_capabilities`, with manifest/skill metadata for intent → skill → tool → service → UI/MCP links. Generate a deterministic projection and fail CI on a stale diff; nobody edits the map by hand. Include an installed-App skill dependency report: package/version, skill, generic read tools, target Tracks, and whether execution has same-App focus (including external HR/Asset Register fixtures supplied through public contracts). | Generated map reconciles the existing catalogues and reports orphan references/unbound tools. App-skill generic-read dependencies and unresolved dynamic calls are recorded for W3.0; external domain content stays outside Core. **Delivered:** [`docs/generated/capability-map.md`](../generated/capability-map.md) (+ `.json`), generator `backend/scripts/generate_capability_map.py` (`--package-root` reports external Apps to stdout), stale-diff gate `tests/test_capability_map.py` (smoke). |

**Qualification custody and redaction.** Q records the actual external exam locator, named custodian, pinned model configuration, secret reference, numeric budget, and split digest in the execution record before live activation. These values are execution inputs, not invented by this plan. Follow [WP-06 retained evidence](evidence/2026-09-22-wp06-resident-flow-contract.md): the compiler rejects raw prompts, completions, messages, credentials, authorization, and tool observations at any nesting depth; local manifests/traces remain in ignored `.qualification-evidence/` until redaction review approves a candidate-specific record. Held-out access is restricted to the exam runner and Q reviewers independent of skill authoring. C5's external-exam boundary remains unchanged.

### Wave 1 — flagship App design, complete and verifiable

| Package | Work | Closes | Exit evidence |
| --- | --- | --- | --- |
| **W1.1 Tags in the build** | Accept `taxonomy.tag_groups` inline on `integral_create_app_track`; add `integral_create_tag` to `_PLAN_TOOLS` with `{{track.id:…}}` scoping; seeds may reference tags by name. Update scaffold weave pattern: selects for workflow state, tags for cross-cutting classification. | G01 | A designed vocabulary exists after build; seeds carry tags; views can filter by them. **Delivered:** `integral_create_app_track.taxonomy` (`{tag_groups: [{name, tags}]}`, normalized by `normalize_inline_taxonomy`) creates each tag through the public tag handler after the entry types, so policy, per-Track name uniqueness, `CONTAINS` from the Track's model, manifest sync and change events match a manual create. Inline tags register as `{{tag.id:Name}}` batch references. `integral_create_tag` (now with `group_key`) is a plan tool scoped to a planned Track. `integral_create_entry.tags` accepts ids or names of the Track's tags; an approved seed with an unknown tag fails rather than dropping it. A saved-view filter on `tags` means membership (`$all`), not array equality. The blueprint's `tag_groups` and seed `tags` are compared structurally. Tests: `tests/test_build_tags.py` (smoke), including an end-to-end build whose tag-filtered view lists only the tagged seed. |
| **W1.2 Anchors in the build** | Compile an App-level `app.track_templates[]` definition before the source EntryType's `relation.target: track`, `target_track_template`, and `auto_provision` binding. The template is registered at build time; the detail Track is provisioned lazily for each parent Entry by the existing anchor materialization path. Explicit links to existing Tracks remain a separate relation choice. | G02 | Two Project Entries receive distinct Project Details Tracks from the same template, each with the intended mixed EntryTypes, structural edges, permissions, and provenance. An empty App creates the template without an unrequested shared detail Track. **Delivered:** `integral_register_track_template` (staged kind `register_track_template`, service `operational_model_authoring.register_app_track_template`, gated on `app.update`) appends a keyed template to the App-attached model's `app.track_templates[]`; `sync_attached_manifest` preserves the registry. It is a plan tool: the build hoists registrations directly after `integral_create_app`, requires every `relation.target: track` field to name an earlier registered key (`slug_manifest_key` of the template name), forces `auto_provision`, and refuses seeds that preset an anchored field — `materialize_anchor_track` provisions one detail Track per parent Entry. The blueprint's `track_templates[]` must be anchored by a field, may not double as a Track, and may not carry views, seeds, widgets or tag groups yet; fidelity compares template fields and anchor targets. Flat relation keys on a field fold under `relation` in both blueprint and plan. Member seeds accept `{{user.id}}` or the caller's auth id and store the graph User id. Design affirmation also accepts natural phrases ("sounds good", "go for it", "that works"), and a bare yes/yep/ok affirms only when it is the entire reply ("ok, make the notes private" still amends). Tests: `tests/test_build_anchors.py` (two parents → two distinct detail Tracks; empty App registers only the template; hoisting; fidelity). Browser smoke: a natural-language studio brief built Projects with per-project "Notes: Acme website" / "Notes: Globex rebrand" Tracks. |
| **W1.3 Structured blueprint** | Give `integral_propose_design` a typed `blueprint` schema (goals, actors, Tracks/EntryTypes/fields/tags/relations, views with the decision each answers, dashboard, skills, routines, seeds, operations needed, access, open decisions) alongside the markdown preview. Give every constituent a stable item ID; encode optional constituents as absent or empty, and record any required platform defaults explicitly. `plan_differs_from_design` compares structurally. | G08 | Amendments are diffs of the blueprint; a dropped field fails preflight deterministically. **Delivered:** `app/schemas/design_blueprint.py` (typed `DesignBlueprint`; entry-type/field ids derive from `<track_id>.<key>` when omitted), `app/services/design_blueprint.py` (validation, digest, item-ID diff, `plan_fidelity_errors`). `blueprint` is required on `integral_propose_design` (its JSON Schema is embedded in the tool contract); each replacement bumps `blueprint_revision` and returns `blueprint_diff`, and amend turns see the prior blueprint. With a blueprint the builder drops every prose heuristic: missing or extra Tracks, field keys, views, seeds, dashboard, skills, and routines are refused by name; open decisions refuse the build; tag groups and track templates are refused as not yet buildable until W1.1/W1.2. Prose-recovered designs keep the legacy checks. Tests: `tests/test_design_blueprint.py` (smoke). |
| **W1.4 Coverage check** | New read tool `integral_check_design_coverage(blueprint)`: classifies every requirement as `native`, `installed extension`, `requires trusted package`, or `unsupported`, validating field/view/widget types and config keys against the live palette. Scaffold calls it before proposing. | G03 (honesty half), G08 | Unknown types fail before the user sees the proposal; operations that need code are named explicitly. **Delivered:** `app/services/design_coverage.py` classifies every field, view, dashboard widget and operation of a blueprint. Verdicts defer to the building code: fields run through compile's own `_normalize_field_spec` (so the registered-but-unbuildable `computed` type is `unsupported`), select fields need options, built-in view config keys must be in `BUILTIN_VIEW_CONFIG_KEYS` (what `normalize_view_config` persists) or a build shorthand, and board/calendar/wiki/table bindings follow the repair pass (select / date / relation field, existing column keys). Widgets resolve through the registry after `TYPE_ALIASES` (moved from `bindings.py` to `app/views/dashboard_widget_types.py`). Plugin and composite types are `installed_extension`; operations and `extension_view` are `requires_trusted_package`. The read tool `integral_check_design_coverage` wraps it. `integral_propose_design` enforces it before recording: any `unsupported` item refuses (`unsupported_design`, listing reasons and the valid palette), each trusted-package item must be named in the proposal (`trusted_package_unnamed`), and a proposal promising an outbound effect no Core tool performs (texts/SMS, payments, webhooks) with no blueprint operation refuses (`code_needs_unlisted`). The marker records `coverage`. Browser smoke: a requested delivery map and automatic texts were each refused before recording, and the resident told the user the map is unsupported and texting needs a trusted package. |
| **W1.5 Build verification tool** | **Contract defined after W1.1–W1.4 freeze the blueprint, constituent, and coverage contracts.** New read tool `integral_verify_build(design_id, design_revision, execution_receipt_id)`: independently read the affected objects using the execution receipt's stable blueprint-item-to-object mapping. Check only the exact approved revision's required constituents plus documented platform defaults. Dashboards, routines, seeds, skills, and either relation form may be intentionally absent. A private skill is checked for availability in its authorized App context. Return a revision-bound verification receipt with per-item `present`, `missing`, `mismatch`, `denied`, or `read_failed` results and overall `verified`, `partial`, `blocked`, or `failed` status. The builder requests verification after successful apply; later amendments or mutations require fresh verification. | G04 | A minimal App without optional features verifies. Missing promised objects yield `partial`; denied or failed reads never become missing objects or a verified result. A changed design revision or mismatched execution receipt is rejected. No duplicate verification call executes the build again. |
| **W1.6 App-skill discovery** | Keep App-private as the default. (a) Routing: when an authorized user's request outside App focus matches a private App skill, the resident is told the skill exists and offers to act in that App's context (or switches focus) — never for users without App access. (b) Blueprint: each authored skill carries an explicit `visibility: app_private \| workspace` choice, defaulting to `app_private`; workspace-wide requires the user to choose it in the design. | G05 | Authorized user outside focus is offered the App skill; unauthorized user gets no hint; workspace-wide visibility appears only after an explicit choice. **Delivered:** `compose_workspace_agent_profile(focused_app_id=…)` overlays an App-private skill as-is only inside its App's focus; for a known caller with access to that App it otherwise overlays in offer-first form (`_offer_first_doc`: name the App, ask, then keep every read/write inside it — naming the App or skill counts as agreement), and a caller without access gets no hint. `get_callable_skills(include_private=True)` keeps the App-access filter while letting the overlay apply focus; the profile cache keys on focus. The build scopes each authored skill from the approved blueprint's `visibility`, never the planner's; the scaffold skill sets `workspace` only when the user chose it, in any language. Usability and follow-through, landed with this item: the persona speaks plain language (no tool names, ids, internal surfaces, or failure internals; one plain sentence when blocked; reply in the user's language; never promise work it does not do in the same turn), and an always-on unmet-need note lets the model — not keyword matching — decide when a described struggle should get a design in the same turn. Follow-through is language-free: a turn whose last acting step failed, or which changed nothing and whose reply a light-model self-check judges as work promised but not done, gets exactly one user-voiced follow-up pass (never persisted, so it cannot count as approval). Repairable build refusals carry `next_tool` through the broker; the build accepts more relation, view, and seed shorthand (sibling Track by name, view `track_hint`, typed seed fields naming an earlier seed); design-diff messages keep the design's own casing. The broker now asks for reauthorization only when the invoked capability's own declaration changed, so an App created earlier in the same run no longer blocks the run's next write. |
| **W1.7 Custom operation bridge** | Phase A: the scaffold emits an **operation spec** (typed inputs/outputs, policy action, idempotency, effects, conflict rule, tests) for any `requires trusted package` requirement, stored as an artifact, plus a package skeleton generated from the SDK template for a developer. Phase B (separate decision): quarantined codegen → test → sign → install through the existing trusted package lifecycle; the resident never claims a tool is live until capability discovery lists it. | G03 | Asset-Register-style protected transition specified from a prose request; skeleton builds and passes its generated contract test. |

### Wave 2 — intelligent capture

| Package | Work | Closes | Exit evidence |
| --- | --- | --- | --- |
| **W2.1 Destination ranking** | New read tool `integral_rank_destinations(text, facets?)`: for each facet returns ranked candidates (App/Track/EntryType), per-field mapping with extracted values, missing required fields, semantic similarity to existing entries, personalization prior, policy eligibility, and a `no_fit` score. Classification stays model-owned; the tool supplies inspectable evidence (schema fit + embeddings + history). The stager remains mechanical. | G16 | On the corpus, top-1 destination accuracy and decoy rejection meet the threshold; the chat shows the "why". |
| **W2.2 Duplicate and link resolution** | The ranking response includes likely existing Entries with match reasons. `integral_file_content` gains `mode: create / update / append`, `entry_id`, expected Entry and schema revisions, relations, and advertised tags. Stage a precise patch: absent fields stay unchanged; append adds the authorized text once to the specified field. Reuse the existing durable operation identity, policy, and protected-field checks; bind retries to the authorized payload and revisions. Content, tags, and relation changes for one facet commit in one supported transaction or are refused before any write. Multiple facets remain separately receipted unless an explicitly supported atomic batch is used. | G18 | Known-person fixtures update/link without duplication. Concurrent edits conflict; response-loss retry appends once; protected fields require their declared operation. An injected failure during tag/relation application rolls back the entire facet, and the receipt never claims a partial facet succeeded. |
| **W2.3 No-fit route** | When `no_fit` wins: preserve the content as a session artifact, propose the smallest structural addition (new EntryType in a relevant Track → new Track in a relevant App → new App via scaffold), and after approval file the preserved content exactly once. Filing SOP gains this branch explicitly. | G17 | Empty workspace, unrelated workspace, and insufficient-type cases each produce the correct distinct proposal; content filed once after approval. |
| **W2.4 Domain-skill precedence** | The overlay exposes each App skill's declared intake domain; the ranking tool returns `prefer_skill` when content matches, and the filing SOP defers. | G16 | HR-style hire description routes to the App skill in the corpus. |

### Wave 3 — query and graph inference

**W3.0 Query boundary decision and parity (gate for W3.1–W3.3).** Close G29 before extending reads. Consume W0.4’s dependency report and inventory installed App skills that currently use generic reads against their own App-domain Tracks (including HR and Asset Register). Decide explicitly whether authorized same-App skill execution receives an allowance, how App context and authority are verified, and how external/MCP callers differ. Skill prose or a caller-supplied App ID must not grant a bypass. Migrate affected skills to declared queries or the approved allowance before enforcement; test same-App, out-of-focus, other-App, paused, and revoked-access cases. Publish compatibility guidance and named package/version migration outcomes. Inventory and test governed queries, QuerySpec, `query_entries`, counts/digests, semantic retrieval, relation reads, dashboards, and exports. The governed query engine currently excludes packaged-App Entry scans; the other generic Entry-read paths do not uniformly apply that package-class restriction. This is distinct from resource permission enforcement. The product architect signs one boundary matrix under ADR-012, including any explicit data-retention/export exception, and the query owner implements it across the existing paths with compatibility guidance. No new tool may bypass it. The following is the proposed target, not a statement of current uniform behavior:

| Target | Core may expose (open, bounded) | App must declare |
| --- | --- | --- |
| Tracks in workspace-authored Apps (no `installed_package_slug`), including scaffold-built Apps | count/sum/avg/min/max/distinct over qualified fields, grouped by platform, `custom_fields.*`, or date bucket; business-field sort; bounded one-hop reads | — |
| Tracks under installed packaged Apps (App domain) | Only explicitly approved, permission-filtered platform metrics; this allowance requires a decision and tests | Business-field aggregates, rankings, and traversals as `kind: query` capabilities with typed parameters; Core executes through the broker |
| Cross-App | One-hop reads over existing relation edges between open-class records | Anything touching App-domain records on either side |

Open question for the decision: whether an App published via D1 and installed elsewhere becomes App domain (and so loses open aggregates) — the answer shapes D1.

**Exit:** the same installed-App fixture, denied Entry, open-class Track, and cross-App relation are exercised through every listed read surface. Each returns the approved result, declared-capability referral, or explicit refusal. A broad mixed query must identify intentionally excluded coverage without revealing inaccessible objects. Tests also cover pause, uninstall, scope changes, and export exceptions. The inventory is updated with the resulting supported contract.


| Package | Work | Closes | Exit evidence |
| --- | --- | --- | --- |
| **W3.1 Aggregation engine** | Per W3.0, one governed service computes `count/sum/avg/min/max/distinct`, qualified grouping and date buckets, with exact totals and explicit over-budget refusal. Expose it as `integral_aggregate` for open-class Tracks and as a typed SDK shape for declared App queries. Freeze numeric semantics: `count` counts eligible records; value aggregates ignore nulls; `distinct` counts distinct non-null typed values; empty count/sum is zero, empty avg/min/max is null. Reject invalid numeric values and incompatible units/currencies unless an explicit conversion capability is declared. Use decimal precision and declared display rounding for monetary values. Bucket datetimes in the supplied IANA timezone with explicit window boundaries; date-only fields retain calendar-date meaning. Define multi-value grouping and deduplication so join fan-out cannot inflate totals. | G09, G10, G19 | Independent expected totals match beyond one page; test nulls, zero, empty groups, mixed types, decimal precision, incompatible units/currencies, DST/date boundaries, duplicate relation paths, denial, and cost overflow. App-domain requests without a declared capability fail closed. |
| **W3.2 Business sort and ranking** | QuerySpec already sorts by qualified `custom_fields.*`. Add the same to `query_entries` with typed comparison, and route insights-skill superlatives to `query_spec` sort + `limit` instead of fetch-and-reason. | G15 | "Highest-value deal" is a single exact call on an open-class Track, correct beyond one page. |
| **W3.3 Relations both ways** | `integral_get_related(entry_id, direction: in / out / both, include_anchors)` returning edge `field_key` and target Track/App. | G11 | Outbound lookups and anchored detail appear with links. |
| **W3.4 Multi-hop traversal** | **Behind gate D2.** QuerySpec v2 with `depth ≤ 3`, implemented as a jvspatial Walker with per-hop policy evaluation, cost ceiling, and path provenance (`via` edges per result). Cross-App hops allowed only over existing relation edges the caller can read. | G12 | Three-hop fixture question answered with cited paths; denied intermediate nodes prune the path without leaking counts. |
| **W3.5 Query planner** | A read tool `integral_plan_query(question)` that returns a proposed plan (instrument, filters, date window resolved against today and the user's timezone, aggregation, traversal) and its limits; the insights skill executes it. Never converts a source failure into "no records". | G13 | Corpus routing accuracy; zero "not found" answers on value-ranking questions. |
| **W3.6 Chainable results** | Accept `result_set_id` as an input scope bound to the original principal, workspace, query class, and schema/policy context, with explicit expiry. Default semantics are fixed candidate membership from the prior result set with current authorized values; return both membership time and value-read time. Revalidate permissions and W3.0 capability restrictions before reading or aggregating; refuse incompatible schema changes. Snapshot-value replay, if supported, must be a separately named mode with retained provenance and current access checks. Follow-ups must never broaden the original candidate set or silently rerun an expired query. | G15 | "Of those, which are overdue?" narrows the original candidates using fresh authorized values. Cross-user/workspace reuse, expiry, revoked access, schema drift, and deleted records have explicit outcomes; no stale cached value leaks after revocation. |
| **W3.8 Filter contract parity** | One filter vocabulary (`field`, `op`, `value`) across saved views, `query_entries`, counts, dashboards, and QuerySpec. Saved-view create/update refuses operators the listing cannot execute (or the listing implements them) and accepts `op` as well as `operator`; `query_entries` advertises the list form. | G30, G31 | A view saved with `in`, `not_in`, or `op` either returns exactly the approved records or is refused before staging; a custom-date range works through `query_entries`. |
| **W3.9 Field-key contract** | Resolve a custom-field reference by key, or by an unambiguous exact label, against the target EntryType; refuse anything else. Applies to `integral_create_entry`/`integral_update_entry` staging (before the card is shown) and to QuerySpec `select`/`filters`/`sort`. A field dropped at execution is reported on the receipt as not applied, never as success. | G32 | `{"Value": 240000}` either lands as `value` or is refused before approval; `sort: custom_fields.Nope` is a validation error, not a null ranking. |
| **W3.7 Scale** | Replace in-memory scans in `query_entries`/`count_entries`/digests with pushed-down queries (`nodes_page`, `count_nodes`, SQL filters). Measure first per AGENTS.md; document any deviation. | G14 | p95 within budget on a 50k-entry fixture. |

### Wave 4 — substrate evolution

| Package | Work | Closes | Exit evidence |
| --- | --- | --- | --- |
| **W4.1 Migration-emitting patch ops** | Add patch ops `rename_field`, `change_field_type`, `rename_option`, `merge_options`, `move_field`, `reorder_fields`, `rename_entry_type` that automatically append the matching publish migration; `modify_field` rejects `key`/`type` changes and points to them. Diff shows value-level impact samples. | G21 | Rename and type change on populated data preserve every value; a lossy coercion is refused with the offending entries named. |
| **W4.2 Field-level `modify_model`** | Either extend `modify_model` to field actions or retire it in favor of the draft lifecycle with a single-op fast path. | G22 | One tool path per edit class in both skills. |
| **W4.3 Bulk move** | Implement `integral_bulk_move_entries` with explicit field mapping, dry-run preview, per-entry outcomes, reference preservation, same-workspace guard. | G23 | Mixed set moves with relations intact or named refusals. |
| **W4.4 Track and tag restructuring** | Bind the existing `PUT /tags/{tag_id}` (rename, reparent) as governed propose tool `integral_update_tag` — agent binding only, no new substrate. New substrate work: a safe tag-merge workflow (retag fan-out, remove source, preview of affected entries), `integral_merge_tracks`, `integral_split_track` (by entry type or filter); all staged with previews. | G24 | Corpus scenarios pass with zero data loss. |
| **W4.5 Contextual improvement** | UI affordance "Improve this" on Entry/Track/View that opens the agent with the focused object and a draft revision; `recommend_customizations` upgraded to suggest fields from repeated body text, selects from repeated values, relations from repeated names, and views from field types. | — | Suggestions accepted in usability sessions; each suggestion stages as one revision. |

### Wave 5 — dashboards that fit the App

| Package | Work | Closes | Exit evidence |
| --- | --- | --- | --- |
| **W5.1 Aggregate data sources** | Dashboard `data_source.kind: aggregate` backed by W3.1 (sum/avg/min/max by group or date bucket on any date field). | G19 | KPI and trend widgets over money and durations match `integral_aggregate`. |
| **W5.2 Schema-aware suggester** | Rewrite `suggest_dashboard_template` to read each Track's schema: select → distribution (pie/bar); date + lifecycle select → due/overdue metrics and trend; number → sum/avg KPI; relation → cross-Track breakdown; routines → upcoming list. Returns widgets with `rationale` (decision supported) and preview values; small honest boards for sparse Apps. | G20, G07 | Corpus Apps receive relevant boards; every widget carries a rationale and its number equals its source query. |
| **W5.3 Widget palette additions** | `table_widget` (top-N records), `metric_card` with aggregate, `chart_line` over a business value, `progress` (target vs actual). Mirror in the frontend registry and view contracts. | G19 | Backend and frontend registries agree (contract sync test). |
| **W5.4 Drill-through** | A single-Track widget opens a filtered Track view; a multi-Track or declared-query widget opens a governed result set with the same scope, capability, predicate, grouping, and temporal semantics. Revalidate access on opening and disclose freshness changes. Numeric KPIs show the contributing records and calculation, rather than implying their row count equals a sum or average. | — | At the same revision/time, record counts and recomputed metrics equal the widget. Cross-Track drill-through retains all contributing Tracks; changed data or permissions produces an explained refreshed result. |

### Wave 6 — reliability and experience

| Package | Work | Closes | Exit evidence |
| --- | --- | --- | --- |
| **W6.1 Skill ownership and routing** | Re-cut descriptions so each intent has one owner; add routing scenarios (ambiguous, follow-up, correction, rejection, resume) to W0.3. | G25 | Routing accuracy threshold met. |
| **W6.2 Durable open batches** | Pending/blessed stages are already durable. Persist open, uncommitted batches (ops, token bindings, owner, session) through the same store so restart resumes assembly; fail closed on schema revision drift. | G27 | Restart between `begin_batch` and `commit_batch` resumes the same batch once; no duplicate ops. |
| **W6.3 Visibility** | Extend the existing Skills settings experience with a turn-specific "Skills and tools available here" view: active workspace/App, loaded skills, available tools, and permission-safe exclusion reasons. Add Mission Control pending approvals/inbox integration. Use the same effective catalogue as runtime dispatch. | G28 | Settings management remains usable; turn visibility changes correctly with scope, App focus, install/pause, and access revocation. Unavailable private capabilities are not disclosed to unauthorized users. |
| **W6.4 Deferred shortcuts** | Decide `workspace_setup`/`onboard_user` against real journeys; implement as compositions over W1 tools or keep dated exceptions. | — | No `gap` tool advertised as working. |

### Decision gates

D1–D3 are optional product and trust-surface expansions. They are excluded from baseline completion and start only after their listed prerequisites and an approved scoped design. D4 is a baseline edit-contract decision and blocks W4.2 only; it is not an optional expansion. W3.0 is the earlier query-contract decision and parity gate.

| Gate | Expansion | Preconditions | Decision inputs |
| --- | --- | --- | --- |
| **D1** | App → library package (`integral_publish_app_as_package`): emit an app-scope Operational Model v2 manifest (tracks, entry types, taxonomy, views, relations, skills, dashboards, routine templates, optional seeds). Closes G06. | W1.1–W1.5 shipped (the package must carry what the build and verifier understand); W3.0 signed. | Trust tier and signing of user-published packages; whether installs become App domain; seed/PII policy; library visibility across workspaces. |
| **D2** | QuerySpec v2 multi-hop (W3.4). Closes G12. | W3.0 signed; W3.1–W3.3 shipped and measured. | Depth ceiling; per-hop policy cost; App-domain hops only via declared capabilities; ADR-012 amendment if the locked-C contract changes. |
| **D3** | W1.7 Phase B: resident-generated executable tools. | W1.7 Phase A proven on two Apps. | Quarantine, review, signing, and rollback path; who may activate. |
| **D4** | `integral_modify_model`: extend to fields or retire (W4.2). | W4.1 shipped. | One edit path per edit class. |

## 5. Manifest changes

| Tool | Op class | Package |
| --- | --- | --- |
| `integral_check_design_coverage` | read | W1.4 |
| `integral_verify_build` | read | W1.5 |
| `integral_publish_app_as_package` | propose | D1 |
| `integral_rank_destinations` | read | W2.1 |
| `integral_aggregate` | read | W3.1 |
| `integral_plan_query` | read | W3.5 |
| `integral_merge_tracks`, `integral_split_track` | propose | W4.4 |
| `integral_update_tag` (binds existing `PUT /tags/{tag_id}`), `integral_merge_tags` (new workflow) | propose | W4.4 |
| `integral_bulk_move_entries` (`gap` → `existing`) | propose | W4.3 |
| Extended: `create_app_track` (taxonomy), `file_content` (mode, relations, tags), `get_related` (direction, anchors), `query_entries` (custom sort), `query_spec` (`result_set_id` scope; depth ≤3 only under D2), `count_entries` (custom group-by), dashboard `DataSourceSpec` (aggregate) | — | W1–W5 |

Every new tool: manifest entry with `policy_action`, parameter schema, service-layer implementation, MCP catalogue exposure, contract test, and a scenario in W0.3.

## 6. Skill changes

| Skill | Change | Package |
| --- | --- | --- |
| `integral_scaffold` | Drift fixes; tags and anchors in the weave; blueprint schema; call coverage check before proposing and `verify_build` after commit; operation-spec path for code requirements; dashboard from the schema-aware suggester. | W0.1, W1.* |
| `integral_filing` | Rank → dedupe/link → stage with mode/tags/relations; explicit no-fit branch handing off to scaffold/model; domain-skill precedence. | W2.* |
| `integral_insights` / `integral_review` | Drift fixes; planner first; `integral_aggregate` for totals and group-bys, typed query sort for superlatives; bidirectional relations; multi-hop guidance only after D2 approval and delivery; cite paths and limits. | W0.1, W3.* |
| `integral_model` / `integral_models` | Migration-emitting ops; single edit path per class; merge/split/move guidance. | W4.* |
| `integral_organize` | Bulk move; tag vocabulary restructuring. | W4.3, W4.4 |
| `integral_dashboards` | Aggregate data sources; rationale per widget; drill-through. | W5.* |
| `integral_entries` | `file_content` update/append awareness; bidirectional related. | W2.2, W3.3 |

## 7. Verification matrix

| Journey | Must hold | Failure / recovery must hold |
| --- | --- | --- |
| New App from prose | Verification matches the immutable approved revision and execution receipt's object mapping: only requested constituents and documented platform defaults are required. Include minimal Apps with no dashboard, routine, skill, seed, or relation; separately test optional features when requested. | Rejected design makes no App/Track/Entry writes; duplicate affirmation has one effect; missing/mismatched objects yield `partial`, denied checks `blocked`, and failed reads `failed`. A template-backed anchor creates distinct detail Tracks for two parent Entries. |
| Existing App extension | App ID and data unchanged; additions attached and queryable. | Stale revision or revoked access blocks apply. |
| Informal dump | Correct facets and destinations; duplicate candidates become updates/links; each facet's content, tags, and relations commit together under the exact authorized revisions. | No-fit preserves content and files once after structure approval. Concurrent edits conflict, append retry has one effect, and an injected tag/relation failure leaves the entire facet unchanged. |
| Schema evolution | Rename/type change/option merge preserve values; dependent views and queries still resolve. | Lossy change refused with named entries; interrupted publish resumable. |
| Query | Every existing read surface obeys W3.0; totals, grouping, ranking, date windows, units, precision, and null semantics match independent expected values. Multi-hop is tested only after D2. Follow-up result sets retain candidate membership and disclose fresh-value semantics. | Denied data never contributes. Failed reads, cost overflow, expired result sets, cross-principal reuse, schema drift, and revoked access have explicit outcomes. No generic tool bypasses packaged-App query restrictions. |
| Dashboard | Each requested widget has rationale; values equal its governed query. Single-Track views and multi-Track result sets reproduce the contributing records and calculation. | Unsupported fields/widgets/units are rejected before staging. Changed data or permissions on drill-through is disclosed; a sum or average is not compared with row count. |
| Custom operation | Spec + skeleton produced; installed package discovered; protected field write refused outside the operation. | Untrusted/tampered package fails closed; no phantom live tool. |

Eval exam (W0.3a/W0.3b): independent held-out domains and repeated seeds; zero tolerance for scope violations, false success, duplicate effects, or silent incorrect writes. Preserve the WP-06 success and safety thresholds; freeze any additional pillar thresholds before scoring a candidate. A new model/configuration requires a distinct baseline.

### Per-package evidence record

Use this template for every package; W0.3a validates required fields (`scripts/validate_qualification_contract.py --evidence <record>`; committed records live in `evidence/packages/<package_id>.yaml`). Use explicit `not_applicable` with a reason for absent receipts/readback in documentation or static-check packages.

```yaml
package_id: W0.1
candidate_sha: <full commit SHA>
owner: <pod, accountable lead, assignee>
fixture: {id: <id>, digest: <digest>, split: <development or held_out>}
inputs: <redacted input or access-controlled reference>
revision: <design/schema/config revision or justified not_applicable>
receipt: <execution/verification receipt IDs or justified not_applicable>
readback: <independent observed state and comparison reference>
assertions: <test IDs, command, pass/fail counts>
limits: <scope, exclusions, budgets, unsupported cases>
live_qualification: <W0.3b report reference or pending/not_applicable reason>
result: <pass, fail, blocked, or incomplete>
```

For live evidence also bind the external corpus/split digest, provider/model/version, harness/configuration digest, repetitions, cost, and redaction-review reference. Never include held-out prompts, expected records, or secret values in Core evidence.

## 8. Dependencies and ownership

This table is the scheduling authority. `B0` means W0.1 + W0.2 + W0.4 + W0.3a have completed their deterministic contracts and published interfaces. W0.3b runs alongside implementation and is not part of B0. A dependency means its contract and required behavior must be available before integration. Design work can overlap, but a package cannot claim completion against a speculative dependency.

W0.1 and W0.2 are delivered as one correction-and-regression cohort: neither is accepted until the paired assertions pass. Their work can overlap; W0.2 does not wait for W0.1 acceptance to author those assertions.

Package IDs are stable identifiers, not execution order: W1.3 precedes W1.1/W1.2, then W1.4 and W1.5.

Ownership boundaries: **A** owns blueprint/scaffold/verification and skill routing; **B** owns query contracts, engines, result sets, and dashboard data; **C** owns schema migration, filing mutations, and graph restructuring; **X** owns durable staging/operation infrastructure; **UI** owns rendered experiences; **Q** owns fixtures, evaluation, and evidence. Secondary owners review and supply adapters within their surfaces. Shared files such as the manifest, bindings, and dispatch are integrated by their designated package owner, with coordinated changes rather than parallel overwrites.

Staffing allocation for two pods of approximately five engineers: **Pod 1 (experience and agent delivery)** owns A and UI, with its engineering lead accountable for both; **Pod 2 (substrate and runtime)** owns B, C, and X, with its engineering lead accountable. **Q** is a cross-pod qualification responsibility led by a designated Pod 2 reviewer, with a Pod 1 reviewer supporting deterministic checks; held-out custodians must not author the evaluated skills. These are responsibility labels, not six additional teams. Before assigning packages, the two leads record actual lead/assignee names and reserve Q capacity in the assignment ledger; Eldon owns product/query-boundary decisions. No personnel names are assumed here.

| Package | `depends_on` | Owner and bounded responsibility |
| --- | --- | --- |
| W0.1 | none | A: documented skill/manifest drift corrections; X reviews staging prose |
| W0.2 | W0.4 | Q: executable skill/manifest conformance checks |
| W0.3a | none | Q: deterministic qualification contracts, split custody, evidence schema |
| W0.3b | W0.3a | Q: external nightly runner, pinned configuration, budget, live baseline |
| W0.4 | none | Q: capability inventory and surface mapping |
| W1.3 | B0 | A: typed blueprint and revision contract |
| W1.1 | W1.3 | A: taxonomy build plan; C supplies compiler/tag integration |
| W1.2 | W1.3 | A: template build plan; C supplies anchor compiler/materialization integration |
| W1.4 | W1.1, W1.2, W1.3 | A: capability coverage and preflight |
| W1.5 | W1.4 | A: revision-bound independent verification; Q supplies expected outcomes |
| W1.6 | W1.3 | A: private-skill discovery and explicit visibility choice |
| W1.7 Phase A | W1.4 | A: operation specification and public SDK skeleton; X reviews execution contract |
| W3.0 | B0 | B: architect-approved query matrix, existing-surface parity, compatibility guidance |
| W3.1 | W3.0 | B: shared aggregate contract and engine, declared-App query adapter |
| W3.2 | W3.0 | B: exact sorting/ranking; A updates insights routing |
| W3.3 | W3.0 | B: bidirectional relation reads with package and resource gates |
| W2.1 | B0, W3.0 | A: destination evidence/ranking; B supplies authorized candidate reads |
| W2.2 | W2.1 | C: revision-bound atomic filing mutation; X supplies durable operation authority |
| W2.3 | W2.2, W1.5 | A: preserved-content no-fit continuation through structure creation |
| W2.4 | W2.1, W1.6 | A: intake-domain routing with private-skill constraints |
| W3.5 | W3.1, W3.2, W3.3 | B: typed query planning; A owns skill execution guidance |
| W3.6 | W3.0, W3.1 | B: result-set identity, expiry, freshness, and authorization |
| W3.7 | W3.1, W3.2, W3.3 | B: measured persistence pushdown and scale evidence |
| W3.8 | W3.0 | B: shared filter vocabulary and saved-view operator validation |
| W3.9 | W3.0 | B: field-key resolution for entry writes and QuerySpec; receipts report dropped fields |
| W4.1 | B0 | C: migration-emitting patch contract and populated-data preservation |
| W4.2 | W4.1, D4 | C: selected field-edit interface; A aligns modeling skills |
| W4.3 | W4.1, W3.0 | C: safe bulk move and reference preservation |
| W4.4 | W4.3 | C: tag binding/merge and Track merge/split; UI supplies previews |
| W4.5 | W4.2 | UI: contextual improvement affordance; C supplies revision proposals |
| W5.1 | W3.1 | B: aggregate dashboard data-source adapter |
| W5.3 | W5.1 | UI: widget registry/renderers; B supplies data contracts |
| W5.2 | W5.1, W5.3, W1.3, W3.3 | B: schema-aware suggestions; A integrates them with the approved design |
| W5.4 | W5.1, W3.6 | UI: Track/result-set drill-through; B supplies identical query semantics |
| W6.1 | W1.5, W2.3, W2.4, W3.5, W4.2, W5.2 | A: final routing integration; Q runs cross-pillar exam |
| W6.2 | B0 | X: open-batch persistence and recovery |
| W6.3 | W0.4, W1.6 | UI: effective turn-capability display and Mission Control approvals |
| W6.4 | W1.5 | A: shortcut implementation or documented dated exception |
| D1 implementation | approved D1, W1.1–W1.5, W3.0 | A: optional App-to-package publication; C/X review compiler/trust integration |
| W3.4 | approved D2, W3.1–W3.3 | B: optional multi-hop query contract and Walker |
| W1.7 Phase B | approved D3, W1.7 Phase A proof on two Apps | X: optional executable-generation trust lifecycle; A supplies authoring flow |

After B0, flagship blueprint work, W3.0, schema migration work, and open-batch durability may start independently. Flagship construction converges at W1.5; query work converges at W3.1–W3.3 and then feeds numerical dashboards. Filing ranking requires authorized discovery, not completion of the aggregation engine. The final integration gate joins these branches at W6.1 and the verification matrix in §7; there is no invented serial dependency from build verification to W3.0.

**Live completion gate:** W1.5, W2.3, W3.5, W4.5, W5.2, and W6.1 may start once their table prerequisites are met, but cannot close until W0.3b has a valid baseline and the corresponding candidate’s pillar scenarios meet the frozen thresholds. A budget/credential outage leaves qualification incomplete while deterministic implementation proceeds. All remaining packages supply deterministic evidence; the final program qualification covers their integrated effects.

Baseline completion requires the non-optional packages and their evidence, including any explicit dated shortcut exceptions. D1–D3, W3.4, and W1.7 Phase B remain excluded unless separately selected; their absence must be reflected in coverage results and user-facing promises. C6 release qualification remains governed by [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md).

## 9. Guardrails

- Preserve `Root → IntegralApp → …` reachability; every new Node (tags in builds, packages, verification records if graph-participating) wires its structural edge at create; log-shaped records are `Object`.
- Policy at the exact resource boundary for every hop, aggregate, group, cached result, widget, and citation.
- Multi-hop computation is a Walker; any bulk-query deviation carries a measured `# deviation:` comment.
- Apply the signed W3.0 boundary consistently: App-domain business reads go through declared queries (ADR-012); any approved platform-metric or export exception must be explicit and tested.
- A declarative skill never gains authority; executable behavior lives only in trusted, signed, versioned packages activated through the existing lifecycle.
- Operational Model revision, migration refusal, and protected-field rules are never bypassed by a conversational request.
- Semantic retrieval discovers evidence; exact governed queries decide counts, totals, dates, and protected actions.

## Source index

Grounded in the [tool manifest](../../backend/app/agentive/tool_manifest.yaml), [core skills](../../agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/), [`scaffold_build.py`](../../backend/app/agentive/tooling/scaffold_build.py), [`dispatch.py`](../../backend/app/agentive/tooling/dispatch.py), [`stagers_filing.py`](../../backend/app/agentive/tooling/stagers_filing.py), [`filing_resolution.py`](../../backend/app/services/filing_resolution.py), [`agent_insights.py`](../../backend/app/services/agent_insights.py), [`query_spec.py`](../../backend/app/agentive/services/query_spec.py), [`entry_relations.py`](../../backend/app/api/entry_relations.py), [`agent_profile_patches.py`](../../backend/app/services/agent_profile_patches.py), [`operational_model_migrations.py`](../../backend/app/services/operational_model_migrations.py), [`dashboard_service.py`](../../backend/app/services/dashboard_service.py), [`workspace_agent_profile.py`](../../backend/app/agentive/workspace_agent_profile.py); and extends [HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md](HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md), [CAPABILITY_BROKER_PHASE_SPEC.md](CAPABILITY_BROKER_PHASE_SPEC.md), [ADR-012](../backend/adr/012-intrinsic-agentive-queryability.md), [WP-06 evidence](evidence/2026-09-22-wp06-resident-flow-contract.md), and the [Full Sweep review](../reviews/2026-09-harness-full-sweep.md). Current implementation and [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md) labels take precedence over this plan's descriptions if they diverge.
