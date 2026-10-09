# Workspace-scoped resident skill overlay

The resident's available App skills depend on the authenticated principal and active workspace. Include only installed active Apps on which the user has an effective role. Workspace membership alone must not expose every private App's instructions.

Namespaced overlay keys separate package identities. Manifest descriptions match on-disk standard skill frontmatter. Descriptions support discovery and bodies load on demand; instruction text cannot widen authority.

Tools and hooks remain package-owned lifecycle registrations. Their execution checks current resource access through ToolContext. Skill visibility is not a replacement for these execution checks.

Binding changes, scope changes, pause/removal, and revocation must refresh the applicable overlay. Preserve the distinction between the default native binding and the jvagent compatibility implementation; the shared tenant boundary applies to both.

Test inaccessible Apps, a second workspace, pause, duplicate keys, missing skill files, stale cached profiles, and current-grant rechecks. See [resident architecture](../product/RESIDENT_HARNESS.md).
