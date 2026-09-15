# Streamline consolidation — execution spec (ADR-004)

**Status:** Ready to execute. Implements [ADR-004](./adr/004-app-composition-domain-apps-capability-bundles-scheduled-skills.md).
Landed already on `feat/streamline-apps-and-capability-bundles`: the ADR + guide
updates, and the `document_render` capability bundle (`5598e63f`). This doc is the
remaining mechanical refactor — retire three apps, fold their content into Sales /
Projects, and Wiki-ify Pricing. Each step notes the exact files and the invariant
it preserves.

> Sandbox note: the agent environment that authored this could not delete files
> or commit repeatedly, so the destructive steps below are written for a normal
> git. Deletions use `git rm` (working tree) — not `git rm --cached`.

## 0. Ground rules

- One branch, phased commits (validate each): `cd backend && pytest tests/domain_apps -q`,
  `black app/ && isort app/ && flake8 app/`, and compile the touched manifests
  (`python3 scripts/sync_bundle_skill_manifests.py` + a `compile_canonical_manifest`
  spot check).
- Existing installed workspaces re-seed in dev/staging (no live migration) — the
  data model changes are additive to Projects and a rename in Sales.

## 1. Retire `produce` (its capability already lives in `document_render`)

- `git rm -r backend/app/profiles/produce`
- `git rm backend/tests/domain_apps/test_produce_render_letterhead.py`
  (coverage now in `test_document_render.py`).
- Move Produce **content** into Sales (below): Specimen Library, Proposal Library,
  and the letterhead asset `produce/assets/letterhead_specimen.docx` →
  `sales/assets/`.
- Anyone with `requires_apps: produce` switches to `document_render` (Sales, §3).

## 2. Retire `client-proposal` (fold into Sales)

- `git rm -r backend/app/profiles/client-proposal`
- Move into Sales: the `engagements` track + `engagement` entry type, the
  `proposal_from_transcript` skill (`skills/proposal_from_transcript/SKILL.md`),
  and the `proposal_resident` persona (`agents/proposal_resident.md`) — merge the
  persona into Sales' resident (one Sales facet).
- Relations that pointed cross-app (`engagement.source_transcript` →
  `sales.discovery_transcript`, `.scoping_document`, `.proposal`,
  `.proposal_artifact`) become **intra-app** relations in Sales — drop
  `allow_cross_app: true` where both ends are now Sales; keep it only for the
  render target if it lands in `document_render` (it does not — see §3, the doc
  attaches to the proposal).

## 3. Sales becomes the single self-contained pre-sales app

`backend/app/profiles/sales/profile.yaml`:

- `requires_apps`: `crm`, `projects`, **`document_render`** (drop `produce`).
- Tracks (self-contained): `discovery_sessions`, `scoping_documents`, **`pricing`
  (Wiki — §5)**, `project_proposals`, **`engagements`** (from client-proposal),
  **`specimen_library`** (was Produce `templates`, keep the "Specimen Library"
  name), **`proposal_library`** (from Produce).
- **Rendered document attaches to the proposal (decision D).** Add to
  `project_proposal`: `rendered_document` (type `file`) and the client-facing
  render inputs it already carries. Retire the separate Produce `artifact` entity:
  the Sales `proposal_from_transcript` / `proposal_from_scope` flow calls
  `render_artifact` (from `document_render`) targeting the **proposal** entry with
  the specimen `template_id`, and the engine attaches the `.docx` to
  `rendered_document`. `render.py` already reads the price block + sections off the
  target entry's `custom_fields`, so point it at the proposal.
- Skills: `scope_from_transcript`, `proposal_from_scope`, `proposal_from_transcript`
  (all Sales now). Update `proposal_from_transcript` body: Specimen Library +
  Proposal Library are **Sales** tracks (no cross-app hop); `render_artifact` comes
  from the `document_render` capability bundle (still called by name — the
  workspace tool-surface bridge already surfaces it).
- Persona: one Sales resident (merge `sales_resident` + `proposal_resident`).
- Seeds: pricing pages (§5), the specimen (letterhead) in `specimen_library`, the
  proposal exemplars in `proposal_library`.
- Keep: `price_engagement` (+ `compute_proposal_pricing`) tools; the
  `engagement_pricing` precompute hook; the `proposal_to_opportunity` /
  `proposal_to_project` transform hooks.
- Tests: `test_sales_price_engagement*.py` stay. Add a Sales render assertion
  (proposal → `.docx`) reusing the `document_render` engine.

## 4. Projects absorbs Pulse's content; retire Pulse

- `git rm -r backend/app/profiles/pulse`
- Add to `backend/app/profiles/projects/profile.yaml`: `plan` and `status_report`
  entry types (from Pulse), under an `objectives` (or `plans`) track.
  **Drop** the `datasets` entry type and `compute_metrics` tool (per decision — no
  consumer today; reintroduce as a `metrics` capability bundle when one is real).
- Drop Pulse's always-on `pulse_resident` and the `plan_rollup` /
  `dataset_metrics` precompute hooks. Recompute is periodic (§below), not live
  mirrors — so `plan.open_count` / `plan.at_risk` become optional, filled by the
  scheduled skill, not a hook.
- Cadence skills → **scheduled skills on the resident harness** (ADR-003), authored
  where the content lives (Projects): `status_rollup`, `weekly_standup`,
  `plan_review`. `data_summary` drops with datasets. These are ordinary declarative
  skills; the recurrence is a scheduled task that runs "execute skill X for
  workspace Y", not an app.

## 5. Pricing → Wiki track (decision C: full decomposition)

Replace the single `pricing_rubric` entry type with a `pricing` **wiki** track
(`view_type: wiki`, `app/views/contracts/wiki.json`) carrying typed pages:

- `rate_catalog` (structured) — the `rate_card.lines` + `cost_basis` the à-la-carte
  pricer reads. **Drives pricing.**
- `discount_policy` (structured) — `discount_rules` (sector/volume, stacking cap).
- `engagement_tiers` (structured) — the fixed-price `tier_catalog`.
- `pricing_method` (prose) — when to use tier vs rate_card vs blend.
- `pricing_procedure` (prose) — approval thresholds, margin floor, playbook.

`price_engagement` (`tools/tier_pricing.py`) changes from "read one
`pricing_rubric` entry" to "assemble from the active pricing pages": resolve the
`rate_catalog` page (structured `rate_card`/`cost_basis`), the `discount_policy`
page, and the `engagement_tiers` page. Keep the `role_effort` fallback + shape
normalizers already in place. Update the `scope_from_transcript` grounding step to
read the **Rate Catalog page** for role_keys (instead of the rubric entry).
Migrate the seeded `sales_rubric_2026h2` fields into the corresponding pages.

## 6. Validate + smoke

- Compile all bundle manifests (sales, projects, document_render) — no residual
  `produce`/`pulse`/`client-proposal` references anywhere (`grep -rn` the four
  names across `app/` + `docs/`).
- `pytest` (Postgres/3.11) green; `flake8`/`black`/`isort` clean; CI drift guards
  (`.ci/*`) clean; `tsc` for the frontend.
- Live smoke (browser): reinstall Sales (+ crm, projects, document_render) in a
  scratch workspace; run transcript → scope → `price_engagement` → proposal →
  `render_artifact` (attaches `.docx` to the proposal); schedule a `status_rollup`
  run in Projects and confirm the resident produces a status snapshot on cadence.
