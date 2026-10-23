# Code plugins and extension views

A code plugin changes a reviewed palette implementation or registry. It requires compatible frontend/backend contracts, types, validation, and behavior tests. A manifest alone cannot invent an unregistered executable renderer.

For App Home content, use the [native Home SDK](../platform/app-home-sdk.md).
Other App-specific operational interfaces may use declarative composition or
the declared, sandboxed Track/Entry extension-view surface. The latter declares
its asset entry and scope and uses Core's host/handshake contract.

All App-specific settings controls MUST use the standard App settings dialog
through `app.settings_schema`. Plugins and extension views must not introduce
a separate settings editor. See the [settings UI contract](../platform/app-settings-ui.md).

Keep asset paths contained, query access scoped, and operations declared. Rendering cannot substitute for backend permission. Validate denied, missing, unavailable, and populated data as well as a successful visual state.

Update registry/manifests and generated contracts together where the build requires them. Do not use dynamic remote React loading as an undocumented shortcut around the extension perimeter. See [palette](VIEW_PALETTE.md) and [extension contract](../platform/extension-contract-v1.md).
