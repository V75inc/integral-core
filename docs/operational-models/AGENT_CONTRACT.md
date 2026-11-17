# Agent authoring contract

All App-specific settings UI MUST use the standard App settings dialog reached
from the App header. Declare configuration in `app.settings_schema`; do not
create settings forms/tabs on Home, dashboards, Tracks, Entries or extension
views. Keep operational forms distinct. If a control is unsupported, extend
the shared renderer through a reviewed contract change rather than introducing
a parallel editor. Follow the [App settings UI contract](../platform/app-settings-ui.md)
for persistence, migration, approval and verification.


An agent can help describe and revise an Operational Model using exposed authoring capabilities. Its output is a candidate artifact, not authority to publish or execute it.

Discover current field types, view types, relation targets, and authoring contracts through substrate introspection. Preserve existing introspection keys and shapes when extending the interface. Supply explicit authorized workspace and target identity; do not derive permission from an ID in generated text.

The template authoring service is deterministic catalog matching and structured fill. The resident's model can perform the reasoning before calling it; the service is not itself a hidden general LLM generation call. Type-hint ambiguity and explicit picker conflicts must be handled honestly.

Apply modifications through the declared patch allowlist and draft/publish lifecycle. Validate fields, relations, views, and impacts before proposing publication. Supported migrations handle old records; forcing publication is a separate destructive authority.

Read the current `patch_operations` returned by substrate
introspection before proposing model patches. Unsupported operations fail
before a review card is staged. A saved-view default is selected through
`integral_save_view` with the existing `view_id` and `is_default=true`; preserve
that target's exact name, type and complete normalized configuration. A label
or layout edit is a separate proposal without `is_default`. Tool discovery
does not grant execution authority: protected writes retain staging, current
target permissions and approval. On an explicit retry, refresh capability
evidence before carrying forward a historical unavailable-tool result.

Skills provide guidance, while Core supplies validation, policy, staging, and receipts. A design-only request must remain a design until implementation is intended. See the [resident delivery flow](../product/RESIDENT_DELIVERY_FLOW.md).
