# Payroll workflow contract

This document is the implementation contract for Guyana (`payroll-app`),
Aruba, BVI, and Curaçao payroll profiles.

## Roster source

Each payroll app has its own payroll employee track because compensation
records and pay-run lines must remain local to that app. The `sync_from_hrm`
field on the app's General settings record controls the source:

- When enabled, the payroll workflow synchronizes active employees from the
  optional HR App into the payroll employee track before validating a run.
- When disabled, only records already on the payroll employee track are used.
- HR App is optional; disabling sync is the supported standalone mode.

## Compensation gate

A pay run cannot be created through the HTTP entry API, an agent/tool entry
write, or a payroll create action unless every active employee in the selected
payroll roster has a compensation record that is:

- linked to that local payroll employee;
- effective on or before the run's period end;
- active (not draft, inactive, void, or cancelled);
- positive-valued; and
- explicitly matched to the run frequency (`monthly`, `biweekly`, or `weekly`).

The response includes the missing employee ids and names so a preparer or agent
can create the required record and retry. Automatically provisioned onboarding
rows with zero salary are placeholders and intentionally do not satisfy this
gate.

The server-side `entry.validate` hook is the enforcement boundary. The
payroll-specific implementation is shared in
`backend/app/profiles/payroll_shared/pay_run_validation.py`; each jurisdiction
profile supplies its own track names and HR sync handler.

## What this does not certify

Passing the workflow gate does not certify jurisdictional tax calculations or
statutory filing acceptance. Those remain governed by each profile's
compliance documentation and effective-dated rate tables. In particular, the
BVI tax implementation and Curaçao statutory exports still require the
jurisdiction-specific work identified in
`docs/product/CURACAO_PAYROLL_COMPLIANCE.md` and the corresponding profile
documentation before production filing use.

Future changes to roster selection, compensation validity, or the pre-write
hook must update this contract and add coverage for both HR-sync-on and
standalone modes.
