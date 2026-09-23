# WP-10 public-developer sprint closure

**Status:** closed as a developer-preview record on 2026-09-22.
**This is not a product release and not foundation-reset C6.**

WP-10 required evidence, release notes, a compatibility matrix, a status
update, and AC-14. Publication stays a separate decision. No tag was pushed.

## AC-14

PyPI and TestPyPI publish jobs verify the built `integral_core` wheel, then
refuse to upload a wheel whose SHA-256 differs from that verified file.
`INTEGRAL_WHEEL_PATH` makes `.ci/verify_artifact_baseline.sh` import that
wheel instead of building a second one. SDK and Asset Register proofs still
build their own artifacts in the same job.

## Compatibility matrix

| Surface | Sprint claim | Limit |
| --- | --- | --- |
| Core wheel | Public package boundary is enforced by the artifact proof | Not a frozen release candidate |
| Python SDK | Separate wheel, public imports | Compatibility follows the current SDK package, not a promised major line |
| Asset Register archive | Independent install against the public contracts | Full operational, restart, and restore journey remains open |
| Extension HTTP, resident, MCP | Shared operation path has contract tests | Not qualified as one deployed candidate |
| Postgres | Conditional update and contract lanes exist | Not a restore certificate for this sprint |
| Live resident app building | Out of this sprint | Foundation-reset WP-06 remains open |

## Release notes for this sprint

The public-developer sprint delivered the extension contract, Asset Register
package, view host, trust and lifecycle checks, schedule dedupe, and the
quickstart trial log. WP-00 through WP-09 were already recorded done in
`SPRINT_STATUS.md`.

Known preview limits, not waived:

- No publication from this closure.
- Foundation-reset WP-06, WP-08, WP-09, and C6 remain open in
  `CORE_FINISH_STATUS.md`.
- The acceptance ledger candidate is not frozen. Its mandatory gates stay
  "Not run" until an exact revision is chosen and those commands are recorded.

## Acceptance

The sprint package is closed. Product publication is not authorized by this
record.
