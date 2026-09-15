# Domain-app tests (quarantined)

These tests validate **bundled product apps** and V75 dogfood scenarios (HR/Payroll, Gmail, QuickBooks, CRM bundle tools, consulting E2E, etc.). They are **not** part of the default Integral substrate gate.

Profiles under `app/profiles/` remain in the repo; only the default `pytest` invocation focuses on foundation/substrate behavior.

## Default CI / local substrate run

From `backend/` (inherits `pyproject.toml` `addopts`):

```bash
pytest -q
```

Domain tests are skipped via `-m 'not domain_app'`.

## Run domain tests only

Default `addopts` excludes `domain_app`; override when running this tree:

```bash
pytest tests/domain_apps --override-ini "addopts=-ra -q --strict-markers --tb=short" -m domain_app -q
```

## Run full suite (substrate + domain)

```bash
INTEGRAL_INCLUDE_DOMAIN_APPS=1 pytest -q
```

Or override the mark expression:

```bash
pytest -m "" -q
```

Library-dependent domain tests still need the disk catalog seeded (same as main `conftest`).
