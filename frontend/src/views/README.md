# Frontend Views Library

This folder is the canonical frontend entrypoint for view contracts and widget
registration.

## Extensibility Model

- Contracts are synced from backend into `contracts.json`.
- Widgets are registered declaratively via `manifests/*.manifest.ts`.
- Registry bootstraps by auto-discovering manifest files with `import.meta.glob`.
- Runtime model is palette-first: views are prebuilt capabilities, configured by profiles.
- Hot-loading arbitrary new view source at runtime is intentionally out-of-scope.

To add a new widget:

1. Add/update backend contract JSON under `backend/app/views/contracts/`.
2. Run `npm run sync:view-contracts` in `frontend/`.
3. Implement or reuse a widget component.
4. Add a `*.manifest.ts` file under `src/views/manifests/` that exports a
   `WidgetRegistration` as default.

No central registry import list is required.

