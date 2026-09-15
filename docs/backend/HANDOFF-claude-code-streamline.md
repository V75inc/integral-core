# Handoff → Claude Code: execute the ADR-004 app streamline

You are picking up a refactor that was scoped and partially landed by another
Claude session running in a sandbox that could not delete files or commit
repeatedly. You have a normal git + filesystem, so you can finish it cleanly.

## Mission

Execute the app-composition streamline defined in
[`adr/004-app-composition-domain-apps-capability-bundles-scheduled-skills.md`](./adr/004-app-composition-domain-apps-capability-bundles-scheduled-skills.md)
by following the file-by-file execution spec in
[`streamline-consolidation-plan.md`](./streamline-consolidation-plan.md). That
spec is authoritative — read both docs first, then implement §1–§6 in order.

Net effect: **Sales** becomes the single self-contained pre-sales domain app;
**`document_render`** is the shared render capability bundle (already built);
**Produce, Pulse, and Client-Proposal are retired**; **Projects** absorbs
Pulse's plan/status content; cadence becomes scheduled skills; **Pricing becomes
a Wiki track** of typed pages that `price_engagement` assembles from.

## Current state (branch `feat/streamline-apps-and-capability-bundles`)

Already committed and green:
- `2c2da3ec` — ADR-004 + guide updates (`app-bundle-authoring.md`, root `CLAUDE.md`).
- `5598e63f` — `document_render` capability bundle (`app/profiles/document_render/`,
  render_artifact + fingerprint_sources, no tracks) + `tests/domain_apps/test_document_render.py`.

Uncommitted in the working tree (commit these first): `streamline-consolidation-plan.md`
and this handoff doc.

**Clean up before starting:** delete the stray `backend/app/profiles/document-render/`
(hyphen) directory — it is an orphaned duplicate of `document_render` left by the
sandbox and must not ship: `git status` will show it untracked; `rm -rf backend/app/profiles/document-render`.

## Guardrails (from repo `CLAUDE.md` — non-negotiable)

- **jvspatial object-spatial contract**: `@endpoint` not APIRouter; `JVSpatialAPIException`
  not HTTPException; Pydantic models in `schemas/`; wire structural edges at Node
  create (I-GRAPH-01); bundle tools reach substrate only via `ToolContext`
  (I-HOOK-01 / bundle facade). Consult `docs/INVARIANTS.md` for substrate-touching
  changes and state which invariants each change preserves.
- **Commit discipline**: phased commits; before EACH commit run the pre-commit
  hooks and fix every lint/format error (`black`, `isort`, `flake8`, `tsc --noEmit`,
  the substrate drift guards), and run the relevant tests (`pytest` for touched
  backend areas — full suite for substrate-touching changes — and the frontend
  suite for touched frontend). A commit must never carry failing lint or tests.
- **NEVER `git push`** without Eldon's explicit say-so. Committing is fine.
- Keep the branch stacked on the client-proposal work already present.

## Definition of done

1. `grep -rn 'produce\|pulse\|client-proposal' backend/app docs` returns no live
   references to the retired apps (allowlist entries excepted).
2. All bundle manifests compile (`python3 backend/scripts/sync_bundle_skill_manifests.py`
   + a `compile_canonical_manifest` spot check on sales/projects/document_render).
3. `cd backend && pytest` green (Postgres + 3.11); `black app/ && isort app/ && flake8 app/`
   clean; `.ci/*` drift guards clean; `cd frontend && npm run typecheck`.
4. Live smoke (browser, backend restarted): reinstall Sales (+ crm, projects,
   document_render) in a scratch workspace; run transcript → scope →
   `price_engagement` (→ ~$179k on the seeded catalog) → proposal → `render_artifact`
   attaching the branded `.docx` to the proposal; schedule a `status_rollup` run in
   Projects and confirm a cadence status snapshot.
5. Report a PR-ready summary; do not push.

## Suggested kickoff prompt (paste into Claude Code)

> Read `docs/backend/adr/004-*.md` and `docs/backend/streamline-consolidation-plan.md`,
> then implement that consolidation on the current branch. First commit the two
> uncommitted docs and delete the orphaned `backend/app/profiles/document-render/`
> dir. Work in phases per the plan (§1 retire produce → §6 validate), committing
> each phase only after lint + the relevant tests pass. Follow repo `CLAUDE.md`
> guardrails, preserve `docs/INVARIANTS.md`, and do not push. When done, run the
> live smoke checklist and give me a PR-ready summary.
