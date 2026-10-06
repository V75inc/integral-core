# Dependency audit disposition

Reviewed 2026-10-06 for the JB/main CI gate (multidict / pymongo / jose /
source-map-js / Tailwind-chain advisories).

The backend production lock was audited from its frozen `uv.lock` export with
`pip-audit --no-deps --disable-pip`. Actionable pins were bumped where fixes
exist (`multidict` → 6.9.1, `pymongo` → 4.18.2). Two matches remain ignored
under explicit disposition:

- **PYSEC-2026-1325** (`ecdsa`): no fixed version; arrives via `python-jose`.
- **CVE-2026-85394** (`python-jose` ≤ 3.5.0): DER public-key-as-HMAC algorithm
  confusion; no upstream release beyond 3.5.0. Mitigated in practice by
  pinning `algorithms=` on JWT verify paths. Revisit when a fixed jose ships
  or the stack migrates off `python-jose`.

The frontend lock resolves high-severity GHSA-vfj7-8cjw-p6xm through
`braces` → `micromatch` → `fast-glob` → `chokidar` → `tailwindcss`, and the
same parent entry also carries GHSA-rj75-hqrm-r3gf (`postcss-selector-parser`
quadratic flat-selector parse; fixed in 7.1.6). Tailwind 3 pins the 6.x
selector-parser line, and npm's available remediation for both is Tailwind 4
— a major build-tool migration that needs its own build and rendered-UI
qualification. The advisory for GHSA-rj75 also scopes ordinary trusted
build-time CSS as out of reach. The audit gate accepts both GHSAs by ID only,
and continues to fail on any different actionable advisory in the same
packages. Reassess before each release candidate; remove when a fixed
compatible dependency is available or the Tailwind migration has passed its
own qualification.

`dompurify` and `source-map-js` were bumped via `npm audit fix` (3.4.16 /
1.2.2) where patch upgrades existed without a major migration.

The separate GHSA-qwww-vcr4-c8h2 disposition remains limited to the React
Server Components route, which the client-only SPA does not use. Its proposed
npm downgrade reintroduces an applicable browser-router issue; revisit when
the supported React Router upgrade is scheduled.

This file records a temporary release-risk disposition. It is not a claim
that the affected packages are vulnerability-free or that the build chain is
runtime-exposed.
