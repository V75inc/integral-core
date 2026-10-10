# Managed installation environment isolation — 9 October 2026

This local candidate builds on staging revision
`fabca0c7234d94f1654ab60f21fbf15e3a51aa42`. The team's exact failing `.env`
was not supplied. The reproduction below establishes a separate, concrete
configuration defect in the managed launcher used by Integral Business.

Before publication, staging advanced to
`3987b3b99d92762a92369fd4077cf31698a89fd1`. The candidate was rebased onto
that revision, preserving its approved field-identity verification and occupied
port fixes, together with both sets of runtime regression tests.

## Defect and repair

The managed runtime inherited all process variables before applying its own
settings. With no `settings.env`, an unrelated terminal model, provider key or
malformed budget could therefore alter or prevent startup. Dotenv interpolation
also resolved references against that same process, silently substituting empty
strings when references were missing.

The launcher now retains only system paths, locale, proxy and certificate
configuration from the process. Operator settings are explicit in this
installation's `settings.env`; workspace model connections remain the normal
UI setup path. References resolve against preceding file assignments and system
variables. Missing references fail with a line number, and test-authentication
settings are rejected before startup. Hosted API/Docker environment loading is
unchanged.

Managed startup also explicitly enables authentication, overriding an operator
file's `JVSPATIAL_AUTH_ENABLED=false` rather than accepting a broken login setup.

Persistent installation identity, signing/encryption keys, authenticated
workspace scope, native harness selection and Core/App separation are preserved.
No graph schema, permission grant, effect or billing behavior changes.

## Evidence

The combined revision passed staged repository guards, configured pre-commit
format/lint/type checks, and `make verify-ci`. Its clean-wheel import and
reproducibility checks passed with SHA-256
`ec4d54b3b1bd8f9cc09199f51fa7b503e6063f0226b9da9c1371be414628cb96`.
The full combined backend suite also passed with the checkout virtual
environment on `PATH` and no inherited `PYTEST_ADDOPTS`. The earlier
qualification sequence is recorded below.

- Controlled before/after subprocess configuration load: with a synthetic
  inherited model/key and `INTEGRAL_NATIVE_TURN_TOKEN_LIMIT=invalid`, staging
  exited 1 and inherited the unrelated model/key. The candidate exited 0,
  excluded both and retained the installation encryption key.
- Local lifecycle tests: 38 passed, including explicit settings precedence,
  interpolation, missing-reference diagnostics, reserved test flags and actual
  child configuration loading from an unrelated working directory.
- `make verify-ci`: passed in this checkout without a developer `.env`, using
  `TESTING=1`, `DEBUG=false` and the smoke marker under xdist. The configured
  Black/isort checks and flake8 checks for changed Python files passed.
- Pre-publication checks also passed all 16 staged repository guards,
  pre-commit hooks, mypy, frontend lint/type checks and the reproducible wheel
  import check. The final wheel, including explicit authentication enforcement,
  reproduced with SHA-256
  `25bf09c1d0d1f5cb7e3485621da225fe9631406c869cc0dd61c201c3e4c0b41e`.
- The first full frontend run hit timing-sensitive failures during heavy machine
  contention (load average above 300 on 10 CPUs). All 45 tests in the four
  affected files passed with `VITEST_MAX_WORKERS=1`. Test assertions and product
  code were unchanged for that rerun.
- The complete frontend suite also passed with that worker limit: 277 files,
  1,578 tests. The remaining suite command was
  `VITEST_MAX_WORKERS=1 make test-frontend test-backend`; the earlier `make verify`
  attempt had already passed its guards, pre-commit, format/lint/types, artifact
  and CI-faithful smoke stages before the frontend contention failures.
- The final-source full backend run had one timing-sensitive failure:
  `test_supervised_heartbeat_during_long_handler` used a 0.6-second lease while
  individual database operations took up to 0.18 seconds under contention. All
  other backend cases passed. The test now keeps its handler blocked until a
  real heartbeat completes, with a normal lease and a short test-only heartbeat
  interval. Production lease behavior is unchanged. Rechecking the complete
  worker and local-runtime files passed all 66 tests, including lease-loss and
  cancellation cases. Final staged pre-commit checks passed again.
- The final-source CI-faithful smoke and clean-wheel import/reproducibility
  checks passed again after explicit authentication enforcement was added.
- Isolated managed PostgreSQL/API/UI startup succeeded while the launching
  terminal supplied conflicting model, key, registration and invalid budget
  settings. Browser login succeeded. Integral AI was selected; sending `Hello`
  without a configured model produced explicit Settings → AI Models guidance.
  No paid provider request was needed.
- Final-source live startup with `JVSPATIAL_AUTH_ENABLED=false` in the operator
  file retained the native harness, rejected an unauthenticated App request
  with 401 and allowed a valid login (200, access token present). The token was
  not included in the evidence.

Evidence files: `/private/tmp/integral-env-isolation-evidence.json`,
`/private/tmp/integral-env-isolation-ci.log` and
`/private/tmp/integral-env-isolation-evidence/chat-setup.png`,
`/private/tmp/integral-env-isolation-final-auth-evidence.json` and
`/private/tmp/integral-env-isolation-final-artifact.log`.

Combined-revision logs are
`integral-env-isolation-rebased-backend.log`,
`integral-env-isolation-rebased-ci.log`,
`integral-env-isolation-rebased-precommit.log` and
`integral-env-isolation-rebased-artifact.log`. Earlier full-suite and recovery
logs are `integral-env-isolation-suites.log`,
`integral-env-isolation-final-backend.log` and
`integral-env-isolation-heartbeat-recheck.log` in `/private/tmp/`.

The temporary browser tab was closed and its isolated installation stopped.
Existing Core and Business installations were not upgraded. These focused
checks are not full release qualification or proof of the team's exact original
configuration failure. Changes are local on
`codex/core-release-foundation-review` during qualification. Pushing the source
does not upgrade the running installations or merge PR #116.
