# Consulting lifecycle demo checklist

Internal demo of **CRM → Projects** (optionally Sales + Portfolio) for client
project management. Platform GTM is still internal/dev — this is a seeded
demo path, not a production readiness claim.

## Bootstrap

1. Start backend + frontend (see root `CLAUDE.md`).
2. Seed Acme (creates CRM, Projects, Sales, Portfolio + fixtures):

```bash
cd backend
python seed_data.py
```

3. Sign in as the Acme org owner (credentials printed by the seeder).

Turnkey install without the full seed: apply workspace profile
`crm-pm-workspace` (CRM + Projects), then optionally install Sales and
Portfolio from the App Catalog.

## Soft independence

| Install | Expected |
|---------|----------|
| Projects alone | Full PM; `project.contact` empty until CRM exists |
| CRM alone | Pipeline / contacts / wiki work; won→project handoff unavailable until Projects is installed |
| Both | Link projects to CRM contacts; won opportunity spawns a Project |

## Demo script (~20–30 min)

1. **CRM Contacts** — open an account/contact (Contoso, Northwind, …).
2. **Opportunities** — open a negotiation-stage deal; note value + contact.
3. **Handoff** — set `stage` to `won` and save (or ask the resident: “mark … won and start delivery”).
   - Auto-transform creates a Project on **Customer Projects** with provenance.
   - Fallback: on a won opportunity, use **Start project** in the entry header.
4. **Customer Projects** — open a client engagement (Contoso, Northwind, …);
   confirm the CRM contact link. **Internal Projects** is the in-house board
   (API hardening, SSO, ops) with no contact.
5. **Anchors** — open Project Details (always auto-created with the project).
   Seeded **customer** projects also get Financials / Contracts (privilege demo).
   Internal projects stay details-only unless you opt in with Create Track.
6. **Sprints** — open the **Sprints** board (team rollup). Contoso Sprint 1 and
   API hardening Sprint A are active; tasks on those projects’ Details boards
   link via the Task **Sprint** field (timebox). Task **bucket** stays the
   workflow lane. From a Project, Related → **By sprint** shows the assignment.
7. **Privilege** — sign in as a seeded non-owner member excluded from financials/contracts; confirm those tracks deny access.
8. **PM skills** — ask for a status rollup or weekly standup on Projects.
9. **(Optional) Sales** — accepted proposal → project transform.
10. **(Optional) Portfolio** — case study linked to a done project.

## Verify handoff without the UI

```http
PUT /api/entries/{opportunity_id}
{ "custom_fields": { "stage": "won" } }
```

Expect a new Project (when Projects is installed). Explicit fallback:

```http
POST /api/entries/{opportunity_id}/transform
{ "hook_key": "opportunity_to_project" }
```

Agent tool: `integral_transform_entry`.
