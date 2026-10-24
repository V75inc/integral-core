# Workspace-scoped resident skill overlay

The resident's available App skills depend on the authenticated principal and active workspace. Include only installed active Apps on which the user has an effective role. Workspace membership alone must not expose every private App's instructions.

Namespaced overlay keys separate package identities. Manifest descriptions match on-disk standard skill frontmatter. Descriptions support discovery and bodies load on demand; instruction text cannot widen authority.

Tools and hooks remain package-owned lifecycle registrations. Their execution checks current resource access through ToolContext. Skill visibility is not a replacement for these execution checks.

Binding changes, scope changes, pause/removal, and revocation must refresh the applicable overlay. The native binding enforces the same tenant boundary for every turn.

Test inaccessible Apps, a second workspace, pause, duplicate keys, missing skill files, stale cached profiles, and current-grant rechecks. See [resident architecture](../product/RESIDENT_HARNESS.md).

## Validated package sources

Library source validation is shared within each process, independently of the
short-lived workspace profile cache. Changes to package artifacts, signing
keys, ordered package roots or plugin registrations invalidate source reuse.
Recorded bundle paths must resolve through this validated index; a rejected
package cannot re-enter through metadata or a same-named skill in another bundle.
Caller access is checked afresh before a cached profile can be returned.

Library validation and Skills editor catalogue compilation run outside the API
event loop. Concurrent cold requests and forced rescans serialize compilation;
forced rescans still validate afresh. Catalogue graph updates and structural
edge callbacks remain on the originating request task. Source reuse never
stores caller permission decisions or workspace content.

## Author skills as complete procedures

Declare the exact callable tool names in the standard space-separated
`allowed-tools` field. Include the reads, contract introspection, proposal
operations and readback needed to complete the workflow. Keep each skill focused;
load another skill for an unrelated workflow rather than declaring the whole
catalogue. Explain inputs and outputs, which results are pending approval, and
how to verify the saved result. Prefer returned typed starter payloads to
reconstructing nested objects from examples. Describe absent capabilities and
partial outcomes truthfully instead of promising work after the reply ends.

The installed Harness Skills capability loads instructions; it does not bind
`allowed-tools` itself. Integral consumes this metadata and uses Pydantic AI's
public per-request tool preparation to disclose the declared schemas when the
skill is active, including when activation is restored from framework history.
This applies equally to reads and change proposals, and supports shared tools.
Only tools already present in the current authorized catalogue can be disclosed.
Unrelated tools remain deferred and searchable. Skill text, tool declarations
and restored activation do not grant authority: every dispatch still checks
current scope, permissions, policies and required approval.

After approval or rejection, verify the recorded result and give one or two
short sentences. State what changed or remains unresolved; omit workflow
recaps, repeated proposals and unsolicited offers. Keep exact technical facts
in diagnostics. Do not truncate an uncertain outcome into a success claim.

### Skill context lifetime

The authorized workspace skill library is a discovery catalogue, not a list of
procedures to resend forever. Native runtime context policy retains the active
and recent task's procedures, expires older complete load pairs under a byte
allowance, and bounds historical tool disclosures. The agent can reload an
expired skill using the normal authorized discovery/load flow. A skill is never
left marked active after its required instruction body is discarded. Pending
staging/effect continuations protect their task, and final context admission
stops oversized protected work safely. App uninstall, permission checks and
profile invalidation remain Core authorities; context retention grants none.
