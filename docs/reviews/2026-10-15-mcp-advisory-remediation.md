# MCP SDK advisory remediation

Status: source qualified for the PR branch; remote CI pending. No advisory
suppression, live runtime change or deployment.

PR #136 head `edd7817d` failed its backend job at dependency auditing. The
frozen lock contained `mcp==1.28.1`; pip-audit reported CVE-2026-59951 with fixed
1.x version 1.30.0. This was not a failed application test. The earlier head's
CI passed; audit outcomes must be checked against the actual current run.

The [upstream advisory](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-84m7-p3x7-pcfv)
describes retained state and unbounded accumulation in stateful Streamable HTTP
sessions. It lists 1.30.0 as patched and excludes stateless HTTP on supported
versions. Source inspection confirms Core's `build_session_manager` already
constructs a stateless JSON session manager. That reachability distinction does
not justify retaining an affected runtime dependency or suppressing the audit.

Raise Core's declared lower bound to `mcp[cli]>=1.30.0,<2` and refresh only that
locked package and required dependencies. Keep the existing `<2` cap: Core uses
the 1.x server decorator API. Do not migrate the protocol major, change Core's
stateless transport, or add domain-specific configuration for this remediation.

Qualification must include the unsuppressed frozen-export audit, new clean
verification environment, MCP server/client/OAuth/connector tests, full default
backend and CI-faithful smoke, native affected contracts where needed, clean
Core/SDK/external-App artifacts and current GitHub CI. Earlier source suites
using the old dependency remain historical compatibility evidence; they do not
qualify the patched environment. No original shared verification environment,
application runtime volume, Business dependency pin or user credential is changed.

## Focused qualification

`uv lock --upgrade-package mcp` changed only the MCP package from 1.28.1 to
1.30.0 and its matching direct requirement; no other locked package version
changed. A new isolated `.venv` resolved the frozen production/dev/test extras
with Python 3.11.15. Runtime readback confirmed MCP 1.30.0 and Core imported
from this isolated checkout, preserving the shared verification environment.

- MCP server/client/OAuth/connector and security-range cases: **188 passed**,
  no skips/failures/errors (`/tmp/core-mcp-security-focused.xml`).
- Unsuppressed frozen-export backend audit: passed, no known vulnerabilities
  (`/tmp/core-mcp-security-audit.log`).
- All four independent artifact gates passed: reproducible public Core wheel,
  clean dependency/ASGI installation, independent SDK wheel and signed external
  App loading (`/tmp/core-mcp-security-artifacts.log`). These gates used the
  isolated prepared interpreter after the earlier system-Python selection
  failure; that failure is retained as historical diagnostic evidence.
- Pinned hooks and backend type checking passed for the dependency/test changes.

At that checkpoint, full backend, CI-faithful smoke and affected native
integration qualification with the patched environment remained pending. Original 19-file consumer suites
continue against their sealed prior dependency snapshot; they cannot be relabeled
as patched-runtime acceptance. No new commit or remote CI success is claimed.

## Expanded qualification and remaining gate

The patched environment passed 106 affected PostgreSQL cases with no skips,
and the revised UI passed 1,829 tests across 321 files plus its production build.
The full default backend ran 7,306 cases: 6,524 passed, 781 skipped and one failed
in the graph-context cross-workspace semantic test. This is not a green full gate.
A clean diagnostic run of that graph file passed. Separately, controlled deadline
regressions reproduced a real classification defect: an incomplete root capture
was reported as a permission denial. Both summary and recorded capture now report
a service timeout while continuing to withhold all data. The cross-workspace
semantic test uses the file's existing complete SQLite fixture so storage timing
does not decide whether semantic assertions are reached. The original failed
run did not retain stop metadata, so its exact cause remains unconfirmed.

The graph fix and subsequent committed-scope bridge refactoring require a new
sealed full qualification run. Prior passed suites remain evidence for their
exact source snapshots, not proof of these later modifications.

## Final combined source gate

After the graph deadline fix and committed-scope bridge follow-through, the sealed
29-file increment passed the full default backend: **6,527 passed, 781 skipped**
(7,308 total, zero failures/errors, 2,085.126 seconds). The same production/test
hashes were verified in the PR checkout. CI-faithful smoke passed **1,778 cases**
with 22 skips and zero failures/errors. Evidence: `/tmp/core-consumers-final-full.xml`,
`/tmp/core-consumers-final-ci.xml` and `/tmp/core-consumers-final-main-reconciliation.json`.

The operation PostgreSQL selection passed 106 cases on unchanged operation source;
the updated graph file passed all 46 cases on PostgreSQL with no skips. These are
named component gates, not the entire native workflow or all skipped journeys.
The final frontend passed 1,829 tests across 321 files, its production build and
TypeScript checks. Lint reported zero errors; the existing warning backlog remains
(467 warnings). All four independent artifacts passed again from the reconciled
checkout (`/tmp/core-consumers-final-main-artifacts.log`). Pinned hooks and staged
architecture guards passed. Documentation updates after this source seal change
qualification records only. Remote CI must be inspected at the pushed head;
no runtime deployment, package publication or complete-goal claim is made.
