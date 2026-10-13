# Host entitlement access

For operator configuration, entitlement APIs, quota/metering hooks and recovery,
see the [commercial host integration guide](../platform/commercial-host-integration.md).
This reference describes the temporary-access and credential-readiness boundary.

Trusted hosts may register `register_entitlement_access_resolver` at boot. Its
async callable receives `(workspace_id, entitlement_key, package_slug)` and must
return a boolean. Only explicit `True` supplies transient operator access. No
resolver or `False` retains durable entitlement enforcement; exceptions and
invalid return types never grant access. The hook is not an HTTP or model tool.

Installed-App enforcement applies to catalog installation records carrying
`installed_from_library_id`. Generic Operational Model authoring and library
merge flows are separate paths; this hook does not establish their commercial
policy. Hosts must review those paths separately when defining package access.

`require_active_entitlement` is used by install, settings finalization, resume,
runtime declarations and execution-safe resource location. Installed contracts
resolve through the active ApplicationDefinition, then the attached manifest.
Execution authorization uses read-only definition lookup without repairing a
legacy scalar pointer; lifecycle callers retain the default repair behavior.
Startup rehydration attempts to pause unentitled commercial installations before
restoring their executable bundle hooks. An active hard dependent may block the
state transition; that package is still skipped during rehydration, and live
execution continues to require its entitlement. Pause dependents first to
reconcile its stored state. Revocation records remain durable even when a
trusted host override currently permits execution.

The override grants no workspace membership, role, direct resource grant or
approval exemption. It creates no Entitlement rows. Retention/management paths
continue to permit generic read/export of paused data. This preserves
I-SUBSTRATE-01, I-EXT-01, I-GRAPH-02, I-APP-02/04 and I-ROLE-01/02.

`resolve_agent_key_access` exposes only read-only route readiness: platform,
validated owner BYOK, or unavailable. It does not return credential material or
update last-used telemetry. Hosts may use it to keep quota notices consistent
with the route execution would use.
