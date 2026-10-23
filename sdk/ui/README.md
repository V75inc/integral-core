# Integral UI SDK

## Native App Home authoring

Home screens render directly with Integral's native components. Use typed
composition functions to package custom layouts or update an installed App:

```ts
import { defineHome, defineComponent } from '@integral/sdk-ui';
const Welcome = defineComponent<{title: string}>(({title}) => ({
  id: 'welcome', type: 'heading', text: title,
}));
const home = defineHome({title: 'Home', renderer: 'native',
  content: Welcome({title: 'Your work'})});
```

Compose stack/grid, cards, text/markdown, data components, forms/actions,
tabs/accordions. Reusable developer functions emit data, not an embedded page or
arbitrary JavaScript running in Core. The resident edits the same composition
contract with preview, ordinary approval and revision guards. Serialize the
result into `app.home` or use the public Home API. See
`docs/platform/app-home-sdk.md` and `examples/reference-sdk-home`.

Run `npm ci`, `npm test`, `npm pack` for an independent typed artifact. This
change does not publish an npm release.

## Existing isolated Track/Entry extension bridge

Framework-neutral TypeScript client for existing sandboxed Track/Entry views. Home uses the native contract above. Use plain
HTML or bundle React/Vue/etc. before packaging. No Core source imports or plugin
registry changes are required.

```ts
import { createClient } from '@integral/sdk-ui';
const ui = createClient<{ heading: string }>();
await ui.ready;
ui.subscribe(({ props, theme }) => render(props, theme));
const { output: records } = await ui.query('records', {});
// Keep one key per user intent across an uncertain-result retry.
const result = await ui.invoke('save', { title: 'Example' }, {
  idempotencyKey: intent.key,
});
await ui.navigate({ kind: 'entry', id: result.output.entry_id });
const stopSizing = ui.observeContentSize();
// On teardown: stopSizing(); ui.dispose();
```

`read`, `query`, `capabilities`, `invoke`, and `navigate` are asynchronous and
accept `signal` / `timeoutMs`. The client matches response type and request ID,
trusts only the parent window and its bound origin, and cancels outstanding
requests when the mount's authority changes. `subscribe` receives current
properties/context/theme without resetting form state. `onEvent` receives
refresh/lifecycle/commit notifications. `resize(number | 'fill')` is explicit.

An operation **never retries automatically**. Timeout, cancellation after dispatch,
and scope changes report `IntegralUIError.outcome === 'unconfirmed'`; they do not
prove a mutation failed. Reconcile current state/receipt first and reuse the
same intent key. A withheld recorded output is a typed error with its receipt in
`error.value`, not an invented successful result. Preview mounts reject business
actions; live query access is a separate opt-in. The server rechecks authority
for every query, operation and navigation target.

## Build a self-contained view

```sh
npm ci
npm test
npm run build
node bin/build-ui.mjs ../../examples/reference-sdk-home/ui/index.html /tmp/home.html
npm pack
```

For a classic script add `<script data-integral-sdk></script>` before your local
script. `integral-sdk-build source/index.html views/home/index.html` injects the
SDK and inlines prebundled JavaScript, CSS and local image/font assets. Bundle
framework imports first. Remote scripts/styles, dynamic imports, external asset
paths and CSS imports are rejected. The served document retains the opaque
sandbox, hash-only script policy and `connect-src 'none'`; data access goes
through the bridge. No authentication token is exposed to the frame.

The bridge remains available for previously declared isolated Track/Entry
assets. It is not the Home rendering path. Authentication, scoped capabilities,
receipts and the opaque sandbox remain unchanged for those assets.
