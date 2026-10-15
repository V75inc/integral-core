# Dependency advisory reconciliation — 2026-10-15

## Evidence boundary

Reviewed Core `cafacbf247a51b42b4dbe30f816bfe502f3fa8c1` with the staged
model-child containment correction. Dependency manifests, locks and audit script
were unchanged during this assessment. The running local Business stack was
not modified. These findings qualify the checked source and lock, not every
consumer's extension code or deployment configuration.

Raw audit evidence is local and temporary:

- `/tmp/core-hardening-live-pip-audit.json`: unfiltered pip-audit JSON of a frozen
  `uv export --no-emit-project --no-hashes`, production dependency selection,
  `--no-deps --disable-pip`. Exit 1 reflects the findings below.
- `/tmp/core-hardening-live-npm-audit.json`: unfiltered `npm audit --json` after
  the locked install. Exit 1; 5 high and 5 moderate package entries. Transitive
  parent entries are not separate root vulnerabilities.
- `uv tree --locked --invert --package PACKAGE`: dependency provenance.
- Installed `jvspatial==0.2.2` authentication source, runtime module identities,
  Core imports, and frontend locked package/source inspection.

## Backend

| Dependency | Current evidence | Required disposition |
| --- | --- | --- |
| `python-jose==3.5.0` | Unfiltered audit reports CVE-2026-85394, no fixed version. It is a direct Core dependency; no runtime Core Python imports it. The two remaining imports are JWT fallback fixtures in `backend/tests/conftest.py`. | Convert these fallback fixtures to the already declared PyJWT dependency, remove the unused runtime dependency, regenerate the lock and qualify a clean environment without jose. Remove the advisory exception after the frozen export is free of the package. |
| `ecdsa==0.19.2` | Only dependency path is Core → python-jose → ecdsa. Audit reports PYSEC-2026-1325, alias CVE-2024-23342/GHSA-wj6h-64fc-37mp, without a fix. Duplicate returned ID entries refer to the same advisory. | Remove with the unused parent; do not manufacture a version bump or retain an unconditional ignore. |
| PyJWT | Already declared `>=2.14.0`, resolved `2.15.1`. Core's auth/service middleware and jvspatial authentication service import `jwt`; jvspatial decoding pins `algorithms=[self.jwt_algorithm]`. | Keep the existing production library and algorithm restriction; qualify fixture token interchangeability. |
| cryptography | Resolved `50.0.1`, retained by independent jvspatial/Authlib/PyJWT/pdfminer dependency paths. The installed jose EC/HMAC implementations select cryptography. | Removal of jose's extra does not itself remove this encryption dependency; verify the regenerated graph and clean install. |

The [python-jose advisory](https://github.com/advisories/GHSA-3qf3-8w2g-rqmx)
describes accepting DER public keys as HMAC secrets when algorithms are not
restricted. The [ecdsa advisory](https://github.com/advisories/GHSA-wj6h-64fc-37mp)
covers timing leakage in signing/key generation/ECDH and explicitly excludes
signature verification. Neither advisory lists a patched release at review time.
Source inspection shows a stronger remediation than the script's existing
mitigation comment: the runtime already uses a different JWT implementation.
This does not prove that arbitrary consumer extensions avoid importing jose.

## Frontend

| Advisory | Locked state and reachability assessment | Required disposition |
| --- | --- | --- |
| GHSA-qwww-vcr4-c8h2 | `react-router` and `react-router-dom` are both `7.18.2`, the patched 7.x version. The current audit has no React Router finding. Core mounts `BrowserRouter`; no RSC server path was found in the inspected entry points. | Delete the obsolete accepted-ID entry and stale statement that a downgrade is the only fix. |
| GHSA-vfj7-8cjw-p6xm | `braces==3.0.3`, through Tailwind `3.4.19` build tooling; advisory has no patched braces version. npm recommends Tailwind 4 as a major dependency migration. | Retain a narrowly documented temporary exception while qualifying that migration. The audited build consumes repository sources; this is not a guarantee about a host that accepts untrusted build inputs. |
| GHSA-rj75-hqrm-r3gf | `postcss-selector-parser==6.1.4`, through Tailwind's build tooling; patched at `7.1.6`. This is a moderate root advisory; npm elevates parent chains due to other findings. | Preserve accurate reachability notes and qualify compatible upgrade/major build migration, rather than downgrading unrelated packages. |
| GHSA-hp3w-g68c-fv3c | `sprintf-js==1.0.3` → argparse → mammoth `1.13.0`; moderate, no patched release listed. Core calls mammoth's browser `convertToHtml`; the inspected argparse import is in mammoth's CLI entry point, not its library entry. | Record this previously omitted moderate finding and its reachability evidence. Confirm a production bundle before declaring it unreachable; npm's suggested mammoth `0.3.29` downgrade is not an accepted repair. |

Primary advisory references:
[React Router](https://github.com/remix-run/react-router/security/advisories/GHSA-qwww-vcr4-c8h2),
[braces](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm),
[selector parser](https://github.com/advisories/GHSA-rj75-hqrm-r3gf),
[sprintf-js](https://github.com/advisories/GHSA-hp3w-g68c-fv3c).

## Audit-gate correctness still to implement

The existing script's header claims an upstream fix automatically invalidates
an exception. Its implementation does not establish that:

1. Backend exceptions are unconditional `--ignore-vuln` flags. A future fix for
   an ignored ID would still be ignored.
2. Frontend accepted IDs are checked before `fixAvailable`; an accepted ID does
   not expire when a fix appears. Version/affected-range and reachability
   assumptions are not machine-checked.
3. Any otherwise unknown high/critical advisory with `fixAvailable: false` is
   silently classified as accepted. Lack of a patch is not evidence that a
   newly introduced risk was reviewed.
4. Moderate advisories are outside the current blocking policy. They still need
   inventory/reachability treatment, especially runtime dependencies.

Replace implicit acceptance with explicit reviewed exceptions and bounded
conditions, fail on unknown findings or audit failure, and keep the complete
inventory visible. Regression coverage must prove a known exception ceases to
match outside its reviewed conditions. Dependency removals/upgrades need clean
installation, runtime authentication, full source and artifact qualification.
No dependency, script or exception modification is claimed by this assessment.

## Isolated qualification and gate reproductions

A separate temporary Python environment installed the frozen exported
requirements with only the `python-jose` and `ecdsa` requirement lines removed,
using `uv pip install --no-deps`. It did not install Core metadata, change the
checkout, or alter the running app. Core was imported from the checked source.
`importlib.util.find_spec` proved both modules absent. The actual
`IntegralAuthenticationService` generated and decoded a synthetic HS256 token;
wrong-key, HS384, expired and unsigned tokens were all rejected. The test used a
new isolated JSON database path and a generated ephemeral key; no live account,
provider credential, or runtime data was used. Install and qualification exited
0 (`/tmp/core-jwt-retirement-install.log`,
`/tmp/core-jwt-retirement-qualification.log`). A second unfiltered pip-audit of
that filtered frozen requirement set also exited 0
(`/tmp/core-jwt-retirement-audit.json`).

This experiment proves the tested import/token operations work without those
two modules. It does not prove the fixture fallback, clean Core wheel metadata,
login/refresh/session protocol or all source tests after dependency removal.
The old fixture imports still need conversion; manifests and lock are unchanged.
The temporary environment path is recorded in `/tmp/core-jwt-retirement-path`.

Two isolated fake-npm reproductions ran the unchanged real audit script copied
into a temporary repository. Both unexpectedly exited 0:

- An unknown high advisory with `fixAvailable: false`.
- The accepted braces advisory ID with a **hypothetical direct braces 3.0.4
  patch** in `fixAvailable`, rather than the currently reviewed Tailwind 4 major
  migration. This fixture is not a claim that braces 3.0.4 exists.

Evidence: `/tmp/core-audit-policy-probe.json`. The earlier coarse diagnostic
used a generic package name/boolean fix flag; the retained reproduction names
the affected package and distinguishes a direct patch from the reviewed major
migration. A boolean `fixAvailable: true` alone cannot establish that an upstream
same-package patch exists: npm can recommend changing a parent dependency.
Future exception matching must preserve that distinction and reject remediation
or package/version conditions outside the explicit review.

## Implementation follow-through

After the containment gates completed, the backend retirement was implemented
in the feature worktree. `pyproject.toml` no longer declares python-jose;
`uv lock` removed python-jose, ecdsa, rsa and pyasn1 without other version changes.
The two fixture fallbacks now import PyJWT, and the backend audit no longer
passes any ignore-vulnerability flags. The original tables above describe the
reviewed baseline, not a claim that these backend dependencies remain required.

The actual regenerated lock synchronized into the separate environment. All
four modules are absent and `uv pip check` passes. Twenty-seven targeted
fixture/auth/session cases and three PostgreSQL identity cases passed with no
skips, and the real unfiltered backend audit reports no known vulnerabilities.
Broader source/artifact qualification and commit are pending. The frontend
policy corrections remain unimplemented; the local preview was not refreshed
with the retirement increment.

### Frontend follow-through

The frontend correction is now implemented in the feature worktree too. The
policy is extracted to `.ci/frontend_dependency_policy.py`, removes the stale
React Router exception, rejects unknown high/critical advisories even without
a fix, and binds remaining exceptions to reviewed locked versions, build-only
flags, root advisory identities/severities/ranges, remediation shape and an
exclusive 2026-11-15 expiry. Missing or cyclic transitive facts fail closed.
Moderate findings remain visible. The original incomplete-policy statements
above describe the reviewed baseline.

Forty-one policy/shell regressions and 49 combined clean-environment policy/JWT
cases passed. Both real audits pass their policies, while frontend explicitly
retains five reviewed high and five moderate package entries. No claim is made
that an unversioned npm boolean flag can expose every future release, or that
static dependency metadata proves arbitrary build-input reachability. The
review expiry and source review remain necessary. This increment awaits broad
source/artifact qualification and commit; it has not replaced the local preview.

## CI observation follow-through at `07dc7750`

GitHub run `38731937581` passed the frontend test suite and production build,
but its advisory step classified all five reviewed high package entries as
outside reviewed conditions. The original log did not retain the policy input
that differed. The merge checkout has the same frontend lock and policy source
as the branch; Node 20.20.2/npm 10.8.2 audit reproduction, including a clean
`npm ci` dependency tree, currently satisfies the unchanged policy locally.
This is counterevidence to assuming a Node/npm version mismatch, not proof that
the remote failure is resolved. Its exact failing input remains unconfirmed.

The gate now prints `REVIEW_CONTEXT` when findings require action: UTC policy
date, review expiry, advisory/remediation input, node paths and locked version/
dev facts. It deliberately omits lock source URLs and other lock fields; a
regression checks that a credential-bearing synthetic `resolved` URL is absent.
No exception condition, severity threshold, expiry or failure behavior changes.
Forty-two policy/shell cases and the real frontend gate pass locally. Fresh
registry inventory contains five reviewed high and six moderate package entries;
these are transitive inventory entries, not eleven independent root advisories.

A new CI run with this observability increment must establish the remote
outcome. Do not call the previous red job green or widen the reviewed exception
without inspecting its actual conditions.

## Observed CI remediation variant qualified

The diagnostic run `38734771030` establishes the exact difference: npm on CI
reports `fixAvailable: false` for braces, chokidar, micromatch,
postcss-selector-parser and tailwindcss. The UTC date and expiry, locked versions,
dev-only flags, advisory IDs/ranges/severity and dependency chains match the
reviewed graph. Local npm currently proposes the already reviewed Tailwind 4
migration. The policy failure was the remediation metadata variant; its deeper
npm/registry/cache origin is not established by this evidence.

`frontend-audit-ci-20261015.json` retains those public observed audit and lock
facts. The policy now accepts the no-automatic-fix flag only for the same
explicitly reviewed Tailwind-major package set after the same version/dev/
severity/expiry checks; every advisory branch still must qualify. This does not
assert the affected packages are fixed or that False means safe. The existing
build-input/reachability review is the risk basis, and the Tailwind migration
remains separate work. Boolean-only fast-glob/postcss-nested conditions are
unchanged. Unknown advisories, runtime placement, changed versions, expiry,
direct patch suggestions and unreviewed migration targets still fail.

The captured CI input first failed its regression test, then passed after the
narrow variant amendment. Forty-eight policy/shell cases now pass with zero
skips/failures/errors (`/tmp/core-audit-ci-variant-green.xml`), including explicit
no-automatic-fix boundary tests for runtime placement, version/advisory changes,
expiry and direct patches. The real frontend audit also passes locally
(`/tmp/core-audit-ci-variant-real.log`); configured type/lint/convention checks
pass and pinned formatting has been applied. Fresh GitHub checks on the next
pushed head remain required before reporting remote CI green.
