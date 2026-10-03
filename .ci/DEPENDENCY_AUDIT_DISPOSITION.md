# Dependency audit disposition

Reviewed 2026-10-03 for the TestPyPI candidate readiness review.

The backend production lock was audited from its frozen `uv.lock` export with
`pip-audit --no-deps --disable-pip`; the live audit reported no known
vulnerabilities and two matches ignored under the explicit PYSEC-2026-1325
disposition. This direct pinned-version scan avoids temporary `ensurepip`
bootstrapping and package re-resolution.

The frontend lock resolves the high-severity GHSA-vfj7-8cjw-p6xm through
`braces` → `micromatch` → `fast-glob` → `chokidar` → `tailwindcss`. The
advisory lists no patched `braces` release. npm's available remediation
requires Tailwind 4, a major build-tool migration that needs its own build and
rendered-UI qualification. The audit gate accepts this GHSA by advisory ID
only, and continues to fail on any different actionable advisory in the same
packages. Reassess this disposition before each release candidate and remove
it when a fixed compatible dependency is available or the Tailwind migration
has passed its own qualification.

The separate GHSA-qwww-vcr4-c8h2 disposition remains limited to the React
Server Components route, which the client-only SPA does not use. Its proposed
npm downgrade reintroduces an applicable browser-router issue; revisit when
the supported React Router upgrade is scheduled.

This file records a temporary release-risk disposition. It is not a claim
that the affected packages are vulnerability-free or that the build chain is
runtime-exposed.
