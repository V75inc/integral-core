# Integral React SDK 1.0

Reviewed packages can ship independently built React components for Home, Track,
Entry compose and Entry detail. Install `@integral/sdk-react` and React 18 as peer
dependencies in the authoring project. Vite 8 and Node >=22.12 build the artifact.
This checkout provides the SDK source; no npm release has been published.

```tsx
import {Button, Text, useAppOperation} from '@integral/sdk-react';
export default function Editor() {
  const save = useAppOperation('save');
  return <><Text>Review your changes</Text><Button disabled={save.busy}
    onClick={() => save.run({title: 'Example'})}>Save</Button></>;
}
```

`useAppUI()` exposes the admitted principal/workspace/App, placement, preview
policy, declared `query`, `invoke`, authorized `navigate`, notifications and
confirmation. `useAppQuery` aborts obsolete consumers and refreshes on host
change notifications. Its typed result exposes `loading`, optional `data`/`error`
and `refetch()`. `useAppOperation` coalesces a pending submission. Operations
never retry automatically. Unconfirmed writes retain the original input-derived
intent key in sessionStorage; check current state/receipt before an explicit
retry. A successful operation is not current-state verification. Withheld output
throws rather than becoming an empty success. `AppUIError.outcome` distinguishes
`rejected`, `unconfirmed` and `withheld`; withheld errors retain the receipt.

`useContributionLifecycle()` exposes mode, entry/track IDs, current fields,
`onDraftPatch` and `register({requestValidate, requestSubmit})`. Return the cleanup
from `register` in an effect. Validate before the host saves its draft; submit
against the committed Entry ID supplied by the host. Do not implement an
additional settings editor: App settings stay in the standard settings dialog.

The public controls include Button, Input, Select, Textarea, Text, Surface,
Stack, Inline and Modal (including Body/Footer). These use host implementations
and tokens. Use `themeTokens` or existing CSS custom properties. Namespace App
styles; do not reset global styles or replace Core navigation/overlay handling.

Use `bin/build-config.mjs`'s `await appUIBuild({entry, outDir})` with Vite's programmatic
`build`. It externalizes React, react/jsx-runtime and this SDK; all other code is
bundled into one ES module. The output check rejects dynamic imports, additional
assets and unsupported static imports. Bundle dependencies locally. The build converts CommonJS dependency reads of shared runtime modules to ES imports. Put styles
in shared controls or namespaced inline styles. No Core source imports are
supported. Core supplies import mappings and preserves public runtime exports.

Declare the artifact, SHA-256, export, `sdk_range: ^1.0.0`, placements and an
object props schema in `app.ui_components`. See the
[platform contract](../../docs/platform/react-app-ui.md) for admission and routing.
The React 18 runtime is part of SDK v1 compatibility; an incompatible React major
requires a new runtime contract and App rebuilds.

For production, produce a normalized `release.json` with `publisher`, `package`
(slug) and the complete normalized `component` declaration. Run
`node bin/sign-release.mjs release.json private-key.pem artifact.mjs` with an
operator-owned Ed25519 PEM private key. It writes the base64 signature beside the
artifact as `.<component-key>.sig`. Keep signing keys outside packages, source control and images.
The backend validates the signature against its configured raw public key.

Native code has Core's browser privileges. SDK APIs are a compatibility boundary,
not a sandbox. Use isolated extension views for external or unreviewed code.

`EntryRegion` composes existing host fields and saved native views inside a
React Entry contribution. Pass either `fields={[...keys]}` with optional
`columns={1|2|3}`, or `viewKey="saved_view_key"`. Regions stay on the current
Entry's track and reuse its typed editors, defaults and draft/submit lifecycle.
The host aggregates every region's validation and persistence handle, blocks
loading/missing regions and preserves recorded outcomes when later work fails.
Entry regions are unavailable in preview. App code does not import view widgets.
An Entry component declaration may set `fallback_view` beside `component_key`
to retain its existing declarative screen while the operator switch is off.

## 1.1 field and lookup contract

Components using `useEntryDraft`, `DateInput` or `AsyncCombobox` require
`sdk_range: ^1.1.0`. SDK 1.0 components remain supported. `useEntryDraft` binds
host-provided metadata, draft values, field errors and retained related drafts
without rediscovering schema. `owns_field_data: true` opts the contribution out
of generic host lookup/default preloads after native admission; its disabled
fallback retains the original behavior.

`useAppQuery(key, params, {enabled, staleTime})` defaults to 30-second freshness;
use `staleTime: 0` for explicit default selection. The host shares scoped
in-flight reads and retains unused cache entries for five minutes. An App read
is not authority to mutate. Async combobox loaders return bounded pages and
opaque continuation cursors; they never fetch the complete collection.

See the [full 1.1 contract](../../docs/platform/react-app-ui.md#react-sdk-11-app-owned-fields-and-bounded-lookups)
and Business's Finance `InvoiceFields.tsx` reference. Build verification checks
these exports in the actual production runtime, including after tree shaking.
