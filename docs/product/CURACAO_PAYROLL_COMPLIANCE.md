# Curaçao payroll compliance coverage

Status: **calculation, payslips, and review-only statutory preparation — not
portal submission automation.**

This document is the hand-off contract for developers and resident agents
working on `curacao-payroll`. It records what the bundle produces today, what
it deliberately does not claim to produce, and the evidence required before
expanding that scope.

## Current coverage

The bundle supports Curaçao pay runs in ANG, using effective-dated frequency-specific tables:

- Effective-dated SVB rates for AOV, AWW, OV, ZV, BVZ, and AVBZ.
- 2026 SVB boundary handling includes weekly/biweekly ZV/OV ceiling conversion,
  the 1% employee AOV upper slice, and post-pension-age AOV withholding rules.
- The complete 2026 monthly wage-tax table, represented as immutable,
  effective-dated `wage_tax_table_row` seed records.
- Per-employee PDF payslips and a ZIP of the pay run's payslips. The PDFs are
  attached to the corresponding Payslip records.
- Payroll-register totals that preserve the per-run wage-tax and premium
  amounts needed for later aggregation.
- Review-only, per-pay-run CSV workpapers for the Tax Office
  wage-tax/AOV-AWW/AVBZ/BVZ return and the SVB ZV/OV declaration. They are
  attached to the Pay Run and must be reviewed and entered in the authority
  portals by a payroll administrator.
- A validation-gated annual `verzamelloonstaat` preparation CSV with WG, WN,
  and DV records, the December 2025 specification's 41-field WN row shape,
  correct WG SVB-number placement, quoted text/date cells, bare numeric cells,
  field-level rejection for incomplete identities, plus KD child and CO/AR
  Article 56 contractor/payment records when configured.
- An SVB ZV/OV preparation worksheet containing the official declaration's
  month/year, employer identifiers, employee count, separate ZV and OV wage
  bases, and total premium payable.

This is enough to calculate and evidence a payroll run. It is **not** enough
to submit statutory returns.

## Statutory artifacts not yet generated

| Artifact | Authority and purpose | Current state |
| --- | --- | --- |
| Periodic wage-tax and AOV/AWW, AVBZ, BVZ return | Curaçao Tax Office online declaration and payment | Review workpaper generated; no official return format or portal submission integration. |
| ZV/OV premium declaration | SVB Employer Forms declaration | Review workpaper generated in the published form's field vocabulary; no portal submission integration. |
| Annual `verzamelloonstaat` CSV | Curaçao Tax Office employee-level annual import | WG/WN/DV plus configured KD/CO/AR records generated; accountant golden-file and portal acceptance validation remain. |
| Employer filing receipt / submission audit record | Evidence that a reviewed return was submitted and accepted | Not captured. |

The Tax Office says its declaration covers withheld wage tax and AOV/AWW,
AVBZ, and BVZ premiums, and its annual December 2025 `verzamelloonstaat`
specification defines WG, WN, DV, KD, CO, and AR records. The SVB publishes
the ZV/OV declaration through its Employer Forms service and says social
premiums are due within 15 days after month end. Treat those sources—not a
plausible-looking generated PDF—as authoritative.

## Why a payslip is not a filing

A payslip is an employee-facing snapshot. A statutory filing needs
period-level totals, employer identifiers, declaration status, and the exact
submission representation accepted by the authority. The annual
`verzamelloonstaat` additionally needs employee identity and employment data
that a payroll total cannot reconstruct. Never label a payslip ZIP, dashboard
report, or generic CSV as "ready to file".

## Data gaps to close before implementation

The annual import specification still has employee declaration fields that are
not fully modelled or validated by the current Curaçao records. The current
generator covers all published record types when source records are configured,
but future work must add and validate the following before claiming complete
annual coverage:

- Employer CRIB number, SVB number, formal/legal name, address, and contact
  number. The profile now has separate trade-name and island fields for the
  SVB worksheet; the annual WG record remains Curaçao island code `1`.
- Employee Sedula/accepted identifier, legal given and family names, date of
  birth, address, island/residency code, gender, marital status, occupation,
  and employment start/end dates.
- Employment classification and the annual fields required by the currently
  published specification: wage, wage tax, AOV/AWW, AVBZ, BVZ, allowances,
  credits, and any applicable special-remuneration values.
- A documented mapping for each optional official field to a source field, including
  currency/whole-guilder rounding and `dd-mm-yyyy` dates.
- The generator now rejects incomplete child/contractor records instead of
  silently omitting them. Continue to keep these source records complete.

The monthly payroll path also needs authoritative confirmation of the correct
wage-tax table variant for each employee (for example, whether a tax credit or
declaration changes the applicable table). The seeded table is the published
2026 monthly table **excluding** the basic tax credit; it must not be assumed
to cover every employee circumstance without that confirmation.

## Required implementation sequence

1. Model missing employee declaration fields and tax-credit status; require
   them before generation rather than substituting blanks or defaults.
2. Build a reviewed, non-submitting monthly Tax Office worksheet and a
   separate SVB ZV/OV worksheet from locked pay-run lines. Reconcile every
   total to the register and display the source pay runs.
3. Add fixtures from authority-published examples or an approved accountant
   test pack, with golden-file tests for each artifact and boundary case.
4. Add a filing lifecycle (`draft` → `reviewed` → `submitted` → `accepted` /
   `rejected`) with the rendered artifact, human approver, timestamp, external
   reference, and any authority receipt attached.
5. Only after explicit security and product approval, evaluate direct portal
   submission. Credentials, MFA, CAPTCHA, and receipt retrieval are a separate
   integration; no agent may represent a generated file as submitted.

## Guardrails for agents

- Say **"preparation worksheet"** or **"export for portal upload"** until the
  corresponding official format and submission path have been verified.
- Do not invent CRIB/SVB numbers, filing periods, worker identifiers, tax
  credits, declaration status, or portal receipts.
- Do not generate a partial annual import. Return the missing-field report and
  ask a payroll administrator to complete the source records.
- Keep old rate tables and generated artifacts immutable. Corrections require
  a new effective-dated rate set or a documented filing revision.

## Primary sources

- Curaçao Tax Office: [Loonbelasting guidance](https://belastingdienst.cw/ondernemer/themas/loonbelasting/).
- Curaçao Tax Office: [2025-12 `Verzamelloonstaat` import specification](https://belastingdienst.cw/wp-content/uploads/2025/12/FIN_Verzamelloonstaat.pdf).
- Curaçao SVB: [Employer guidance and ZV/OV declaration](https://svbcur.org/en/werkgevers/).
- Curaçao SVB: [employer forms](https://svbcur.org/en/formulieren/).
- Curaçao SVB: [2026 premium percentages and wage limits](https://svbcur.org/wp-content/uploads/2026/01/Tabel-2026.pdf).

Review these sources at least annually and whenever an authority changes a
form, portal, file specification, rate, or deadline.
