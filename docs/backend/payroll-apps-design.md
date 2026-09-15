# Payroll apps — one per country: the standard design

**Status:** Canonical pattern for every country-specific payroll app
(`payroll-app` / Guyana, `aruba-payroll` / Aruba, and whichever comes
next). Read this before building a new one — it's the source of truth for
"how does a payroll app work here," not any one app's own profile.yaml.

**Related docs:**

- [app-bundle-authoring.md](./app-bundle-authoring.md) — general bundle
  scaffolding, trust tiers, hook/tool wiring
- [../content-profiles/REGION_SYSTEM.md](../content-profiles/REGION_SYSTEM.md)
  — the broader "one app per region" pattern this specializes

---

## Why one app per country, not one payroll app with a region switch

Statutory payroll rules differ across countries in *shape*, not just rate
values — Guyana's NIS/PAYE math (progressive PAYE bands, a flat personal
allowance baked into the first band) is a genuinely different calculation
from Aruba's AOV/AZV/SVb + wage-tax withholding (flat social-security
rates, a separate Basic Allowance subtraction, an annualized/YTD-projected
withholding spread). Forcing both into one calc engine behind an
`if country == ...` branch produces a worse app than two small, focused
ones. **Each country gets its own bundle** under
`backend/app/profiles/<country>-payroll/`, sharing nothing but the pattern
below and the HR App's employee roster.

## The collision problem — and its one fix

Every payroll app installed in a workspace shares the SAME two lookup
mechanisms with every other app in that workspace:

- `ToolContext.find_entries_in_track_type(track_type)` /
  `find_track_id_by_title(track_type)` match by **track TITLE, workspace-
  wide** — not scoped per installed App (see
  `app/services/hooks/registry.py`'s `_tracks_by_title`).
- Per-workspace **tool registration** (`register_workspace_tools`) is a
  flat dict keyed by **tool key** — also not scoped per app. The second
  app installed silently overwrites the first's registration for any
  shared key.

Two country payroll apps with identically-named tracks or tool keys
("Pay Runs", "Compensation Records", `generate_payslips_for_pay_run`, …)
installed in the same workspace **will cross-contaminate** — this isn't
theoretical, it happened building Aruba Payroll and was caught by a real
test (`tests/domain_apps/test_payroll_apps_coexist_in_one_workspace.py`)
before it shipped.

**The fix, and the rule going forward:**

1. **Every track this app owns gets a country-prefixed display title** —
   "Guyana Pay Runs" / "Aruba Pay Runs", "Guyana Statutory Rates" / "Aruba
   Statutory Rates", "Guyana Payroll Employees" / "Aruba Payroll
   Employees", etc. — **including its own employee roster**. Payroll is
   decoupled from the HR App (`hr_app` is a soft `requires_apps` dep, not
   hard) — every country app owns its OWN `<Country> Payroll Employees`
   track, which Compensation Records / Payslips / Pay Run Lines link
   against directly (same-App relation, never `target_app: hr_app`). The
   HR App's shared `Employees` track is still the one intentional
   workspace-wide exception, but only as a **mirror source**: when the HR
   App is installed and that country app's `sync_from_hrm` setting is on,
   a hire on hr_app's `Employees` track mirrors one-way into EVERY
   installed payroll app's own roster (`tools/hrm_sync.py`'s
   `mirror_employee_from_hrm` hook, bound on the shared `employee` entry-
   type key). Without the HR App, a country app's own Payroll Employees
   track is edited by hand — Payroll never requires HR to function.
2. **Every tool key this app declares gets a country prefix** —
   `aruba_generate_payslips_for_pay_run`, not `generate_payslips_for_pay_run`.
   The underlying Python function name doesn't need the prefix
   (`handler_ref: tools.generate_payslips:generate_payslips_for_pay_run` is
   fine) — only the manifest `key:`/`tool:` strings that land in the
   shared per-workspace registry need to be unique.
3. **Write a coexistence test for every new country app** — install it
   alongside at least one other payroll app in one workspace, exercise its
   generate/populate flow, and assert zero leakage in either direction
   (see the Aruba/Guyana test above for the shape to copy).

A track's manifest `name` is just its INITIAL title — an already-
installed workspace's Track node doesn't pick up a rename automatically.
If you ever need to rename a track after apps are already installed, see
`content_profile_merge.py`'s `provision_prescribed_tracks_from_app_
manifest` (it syncs an existing Track's title from the manifest on
`update_app_from_library`) and `backend/scripts/migrate_guyana_payroll_
track_titles.py` for the migration-script shape.

## Track shape (seven tracks, every country app)

| Track (country-prefixed) | Entry type(s) | Holds |
|---|---|---|
| `<Country> Pay Calendar` | `pay_calendar` | Cadence (monthly/biweekly/weekly), an anchor period start, pay-date offset. One record per cadence the company actually runs. |
| `<Country> Payroll Employees` | `employee` | This app's own employee roster — Compensation Records / Payslips / Pay Run Lines all link here, never at `hr_app` directly (decoupled — see "The collision problem" above). Mirrored one-way from the HR App's `Employees` track when it's installed and `sync_from_hrm` is on; editable by hand otherwise. `hrm_source_employee_id` is a soft cross-App relation (`target_app: hr_app`) back to the source HR record — set automatically by the mirror, or by hand to link a pre-existing local record after adding the HR App later. |
| `<Country> Compensation Records` | `compensation_record` | **Stable rate identity only** — base salary, currency, pay frequency, effective date. New rate = new dated record; never edit an old one. Period-specific figures do **not** live here (see Pay Run Lines below) — an earlier Aruba v1 draft put sickness-pay/fringe-benefit/children fields on Compensation Record and had to move them once the register redesign landed; don't repeat that. |
| `<Country> Pay Runs` | `pay_run` | One row per pay period: `period_start`/`period_end`/`pay_date`/`status` (draft → approved → paid), `gross_total`/`headcount` rollups, and the `pay_run_lines_track` anchor field (see below). |
| *(anchored, not a top-level track)* `<Country> Pay Run Lines` | `<country>_pay_run_line` | One row per employee in ONE Pay Run — the Payroll Register. Auto-provisioned per Pay Run via the anchor pattern. Editable per-period inputs (sickness pay, deductions, children, …) + a full server-computed gross-to-net breakdown, both live-recomputed on every save. |
| `<Country> Payslips` | `payslip` | One row per employee per FINALIZED Pay Run — a snapshot copy of that employee's Pay Run Line at the moment Generate Payslips ran, plus a rendered PDF attachment. Immutable history; a Pay Run Line is ephemeral working data, a Payslip is the record of what was actually paid. |
| `<Country> Statutory Rates` | one entry type per distinct rate concept (see below) | Config-as-wiki — every number the calc engine uses, effective-dated. |
| `<Country> Company Profile` | `company_profile` | Employer identity (name, address, TIN/registration, logo) rendered on payslip PDFs. |

## The anchor + wizard + register pattern

This is the part worth copying exactly, not reinventing per country:

1. **`pay_run.pay_run_lines_track`** is a `relation` field with
   `relation.target: track`, `target_track_template: <country>-pay-run-
   lines`, `auto_provision: true` — the same anchor-track pattern
   `nis_schedule`/`paye_filing` already use for their own employee lines.
   Saving a new Pay Run auto-creates its own private line track.
2. **`pay_run`'s `create_wizard`** has three steps:
   - `period_picker` — proposes the next period (via a
     `<country>_compute_upcoming_periods` tool reading the Pay Calendar),
     but **`period_start`/`period_end`/`pay_date` always render as real,
     editable date fields alongside the dropdown** — never write-only,
     computed-only values. (This was a real bug fixed on Guyana Payroll:
     the wizard originally only offered a fixed-cadence dropdown with no
     way to set an irregular period by hand — see
     `frontend/src/components/entries/CreateWizardModal.tsx`'s
     `period_picker` branch.)
   - `entry_checklist` — pick which active employees this run includes,
     filtered to whoever's Compensation Record `pay_frequency` matches the
     chosen cadence.
   - `summary` — read-only review before create.
   - `on_create_tool: <country>_populate_pay_run_lines_for_employees` with
     `on_create_ids_param: employee_ids` creates one bare line per checked
     employee once the Pay Run itself is saved.
3. **The `payroll_register` view type** (`app/plugins/payroll_register/`
   — generic, not payroll-specific code) renders the line track as a
   grouped, subtotaled spreadsheet: `group_by` a category field (Guyana:
   employee/consultant; a country with only one category still declares
   the field, defaulted, for structural consistency — see Aruba's
   `category` field), `identity_columns` resolved through the `employee`
   relation, `input_columns` editable inline, `computed_columns` read-
   only, `subtotal_columns` summed per group + grand total. Bind it via
   `- view: ':anchored_track/payroll_register'` on the Pay Run's
   `related_views`.
4. **`<country>_recalc_pay_run_line`** — an `entry.create`/`entry.update`
   hook on the line entry type. Every save: re-mirrors the linked
   Compensation Record's stable fields, re-runs the full calc engine, and
   rolls `gross_total`/`headcount` up onto the parent Pay Run. Skips
   (no-op) once the parent Pay Run's `status == "paid"` — numbers freeze
   once money's gone out.
5. **Manual-override shadow keys.** A preparer can hand-edit any computed
   cell. To keep that edit from being silently reverted on the next
   unrelated save, the hook writes a shadow value (`_{field}_calc_value`)
   alongside every computed field it writes. Before overwriting a field
   next time, compare its current stored value to its shadow — if they
   differ, a human changed it since; respect that value this pass (Guyana
   additionally feeds an override forward into downstream fields via
   `manual_overrides`; Aruba's simpler per-field-key-matches-result-key
   shape doesn't need the translation table Guyana's does, but the shadow
   mechanism itself is identical).
6. **Generate Payslips reads the register — it never recomputes.** The
   action-bar button (`<country>_generate_payslips_for_pay_run`) copies
   each already-computed line into a new Payslip, renders its PDF, and
   flips the Pay Run from `draft` to `approved` (never to `paid` — that's
   a separate, human-confirmed event). Skip any line with no linked
   employee, no resolved Compensation Record, or `gross <= 0` (an un-
   funded, still-$0 default record) — never manufacture a $0.00 payslip.

## The calc engine

- **Pure functions, no `ToolContext`, no I/O** — `_net_pay_calc.py`.
  Every rate is a parameter (dataclasses like `SocialSecurityRates`/
  `WageTaxBand`), never a hardcoded constant, so the whole thing is
  independently unit-testable and the config-as-wiki rule below actually
  holds.
- **`_statutory_rates.py`** — the reader for the `<Country> Statutory
  Rates` track: one function per rate concept
  (`latest_social_security_rate_page`, `latest_wage_tax_band_pages`, …),
  each picking the latest page with `effective_date <=` the period in
  question. **Never edit an existing rate page** — a rate change is always
  a NEW page at a new `effective_date`; editing one in place retroactively
  changes every past calculation's implied rate.
- **`_payslip_pdf.py`** — pure PDF rendering (no ToolContext), reused
  structurally across countries (same boxed-slip layout); only the
  deduction line items differ per country's statutory breakdown.

### Establishing a new country's formula — do not guess

The single most important discipline on this pattern: **a country's
calculation formula must be reverse-engineered from real, verified payroll
data before any code is written** — real payslips, real tax-admin reports,
cross-checked to the cent against stated totals (Net Wage, Annual Taxable
Income, Annual Tax) — never assumed from a rate sheet alone, and never
guessed at when a detail is ambiguous. Aruba's formula came from 4 real
Celery-issued payslips + 3 real "Calculations tax and fiscal allowances"
tax-admin reports, reconciled line by line; the resulting confidence
(exact-to-the-cent on 6 of 7 real examples) is what makes the golden-value
tests below meaningful. Where a real example doesn't fully pin something
down (e.g. Aruba's SVb-base treatment of sickness pay, or the exact
period-withholding-spread rounding), **say so explicitly in the module
docstring** rather than silently picking a plausible-looking formula —
flagged-but-functional beats confidently wrong.

## Testing checklist for a new country app

1. **Golden-value unit tests on the pure calc function** — reproduce every
   real payslip/report example used to derive the formula, asserting
   exact figures (small `pytest.approx` tolerance only where the real data
   itself showed an unexplained few-cent variance, and say why in the
   test docstring).
2. **End-to-end install + wizard/register flow test** — real DB, real
   `install_app`, walk the actual sequence a user would: fund a
   Compensation Record, add a Pay Calendar entry, create a Pay Run
   (asserts the line track auto-provisions), populate a line, assert the
   `recalc_pay_run_line` hook computed it correctly, Generate Payslips,
   assert the Payslip matches.
3. **Coexistence test** — install this app alongside at least one other
   payroll app in one workspace; assert no track-title or tool-key
   collision (see "The collision problem" above).
4. **Skip-unfunded-employee test** — an employee still on their $0
   auto-provisioned default Compensation Record must not get a payslip.

## Skills (resident-harness / MCP surface)

Every country app ships at least two `SKILL.md` files under
`backend/app/profiles/<country>-payroll/skills/`:

- `run_<country>_payroll` — create a Pay Run through the wizard sequence,
  adjust an employee's per-period Pay Run Line inputs. **Generate
  Payslips has no MCP path** — it's wired only to the action-bar button,
  so the skill must say so explicitly and never claim payslips were
  generated.
- `<country>_statutory_rates_update` — add a new effective-dated rate
  page/set; never edit one in place.

Both must call out the country-prefixed track titles as the
disambiguator when more than one payroll app is installed. See
`app/profiles/aruba-payroll/skills/` for the current reference shape.

## File manifest (what a new country app looks like on disk)

```
backend/app/profiles/<country>-payroll/
├── profile.yaml                    # tracks, track_templates, tools, hooks, seeds
├── skills/
│   ├── run_<country>_payroll/SKILL.md
│   └── <country>_statutory_rates_update/SKILL.md
└── tools/
    ├── __init__.py
    ├── _net_pay_calc.py            # pure calc engine
    ├── _statutory_rates.py         # Statutory Rates track reader
    ├── _payslip_pdf.py             # pure PDF renderer
    ├── generate_payslips.py        # reads the register, snapshots Payslips
    ├── pay_run_cadence.py          # compute_upcoming_periods, create_next_pay_run
    ├── pay_run_line_calc.py        # populate_*, recalc_pay_run_line
    └── provision_compensation.py   # auto-provision + backfill starting Compensation Records

backend/app/profiles/<country>_payroll/__init__.py   # import-path alias — see
    # payroll_app/__init__.py's docstring for why (I-BUNDLE-04 keeps the
    # bundle DIRECTORY hyphenated to match package.slug; this underscore-
    # named sibling package redirects its __path__ at the real directory
    # so literal `from app.profiles.<country>_payroll.tools.x import y`
    # syntax has a valid spelling — a hyphen can't appear in an import
    # statement).
```

## A gotcha worth knowing before touching any of this: mypy + two
hyphenated bundle dirs

Two hyphenated-slug bundle directories (`payroll-app`, `aruba-payroll`)
collide under mypy's `--explicit-package-bases` — neither can carry a
root `__init__.py` (a hyphen isn't a valid Python identifier), so both
bundles' `tools/` packages infer the same bare top-level module name
`tools`, and mypy fatally errors ("Duplicate module named 'tools'" — exit
2, not just a warning) the moment a THIRD such bundle exists too. Current
fix: `.ci/mypy_backend.sh` excludes `aruba-payroll` from the static-typing
pass (real correctness there comes from the domain_apps test suite, not
mypy). A third country app will very likely need the same treatment —
add its own `--exclude` entry rather than trying to solve the underlying
collision generically.
