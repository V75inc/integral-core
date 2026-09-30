# Integral Desktop

Standalone Electron shell around the Integral web frontend. It renders the
same React app as the browser build and talks to an Integral backend over
HTTP + WebSocket — local by default, remote by configuration.

## Prerequisites

- Node 20+
- A running Integral backend (`cd ../backend && .venv/bin/python -m app.main`,
  default `http://localhost:4000`)

## Run it

```bash
cd desktop
npm install

# Terminal 1 — backend (from repo root)
cd backend && .venv/bin/python -m app.main

# Terminal 2 — web frontend dev server (from repo root)
npm run dev --prefix frontend        # http://localhost:9006

# Terminal 3 — desktop shell in dev mode (loads the dev server, hot-reloads)
npm run dev
```

Dev mode loads the Vite server, so the dev server's own proxy handles
`/api` + `/ws` and the backend URL config is bypassed.

## Bundled frontend (no dev server)

`npm start` and packaged builds don't need the Vite server at all — the
React app ships inside the shell:

```bash
cd desktop
npm run build:renderer   # frontend build (relative asset URLs) → desktop/renderer/
npm start                # loads renderer/index.html from file://
```

`build:renderer` sets `INTEGRAL_DESKTOP_BUILD=1` so Vite emits relative
asset paths, and bakes `VITE_API_URL` (default `http://localhost:4000`,
override at build time) as a fallback — inside the shell the preload
bridge always wins at runtime (see below). Re-run it after any frontend
change; the stale-build auto-reload is disabled in the shell because a
`file://` reload can never fetch a new bundle.

**Backend CORS.** A `file://` renderer sends `Origin: null`, which the
backend's explicit CORS allow-list rejects. Start the backend with the
desktop opt-in when serving the bundled app:

```bash
INTEGRAL_DESKTOP_CORS=1 .venv/bin/python -m app.main   # from backend/
```

(Keep it off otherwise — `null` is also sent by sandboxed iframes. See
`INTEGRAL_DESKTOP_CORS` in `backend/app/config.py`.)

## Local environment access

Integral Desktop can expose a deliberately small, read-only local tool surface
to the resident harness. This is independent of desktop CORS and fails closed
at both ends.

1. Start the backend. Packaged `file://` builds also need the CORS transport
   opt-in:

   ```bash
   INTEGRAL_DESKTOP_CORS=1 \
   .venv/bin/python -m app.main
   ```

2. In the desktop app's **native application menu**, choose **Environment →
   Enable local environment access**. On macOS this is in the system menu bar
   at the top of the screen. On Windows/Linux press <kbd>Alt</kbd> to reveal
   the auto-hidden application menu.
3. In that same native menu, choose **Environment → Grant Folder…** for each
   folder Integral may read.

The application persists this choice in its own `settings.json`; there is no
backend environment-capability flag. Browser sessions have no Electron host
binding and therefore receive no desktop tools.

The initial capability set is read-only for files: granted-root listing, directory
listing, text-file reading, bounded path search, bounded text search, and
allowlisted runtime diagnostics. Paths are always relative to an opaque root
id; absolute paths, `..`, and symlinks resolving outside a grant are refused.
The React renderer receives no filesystem primitive — Electron main owns the
host and connects with a short-lived, single-use backend ticket.

Desktop tools appear only on chat turns sent by the connected Electron shell.
A browser session, a disconnected shell, a different user/workspace, or a
turn without the live application binding sees no desktop tools.

## Computer use (Cua Driver)

Integral Desktop includes a pinned
[Cua Driver](https://cua.ai/cua-driver) runtime. Users install only Integral;
there is no separate Cua installer, PATH setup, or runtime download.

Release CI provisions the platform executable into `resources/driver`, verifies
the published SHA-256, and packages it outside the asar. `npm run pack` and
`npm run dist` refuse to build when that resource is absent. See
[`resources/driver/README.md`](resources/driver/README.md).

### Trust boundary

The remote backend never receives an MCP connection or the full Cua tool
catalog. Electron main owns a `CuaDriver.createPrivateWorker()` process over
inherited pipes and exposes three authored observation calls over the existing
authenticated Integral WebSocket:

- approved application list
- on-screen windows for an approved application
- one exact window snapshot
- the active local lease (`grant_state`)
- optional background `click` / `type_text` / `press_key` / `hotkey` when the
  user separately approves an action lease

Every active runtime starts in Cua `bounded` mode with a manifest generated from
the native local approval. Full-display capture stays disabled. A backend grant
cannot create or widen local authority.

Open **Settings → Computer use**, choose running applications, choose whether
selected-window screenshots may leave the device, optionally allow background
clicks and typing, and approve a duration. Optionally enable **Jev** and save a
TypeSafe API key from the TypeSafe console; the key is stored in the OS
keychain on this device. Jev then chooses the next bounded native action from
the accessibility tree (`driver__choose`); Integral still executes through Cua
Driver. Screenshots and element tokens are not sent to TypeSafe.
Access ends on expiry or explicit revoke. Disconnect and quit stop the worker
but remember the last approved apps and remaining duration on this device, and
restore that lease when the desktop environment reconnects.

Screenshots do not travel as base64 control messages. Electron writes a bounded
local artifact, sends digest-identified binary chunks, and deletes the local
temporary file. The backend keeps the ordinary 1 MiB control-message cap and
exposes the verified screenshot through a short-lived, principal/workspace
scoped, no-store URL. Screen and accessibility content is marked untrusted.
Non-local backends must use HTTPS/WSS; plain HTTP is accepted only for loopback
development.

This phase can observe approved windows and, with a second local consent, act
in the background. Foreground escalation, application launch/termination, and
browser automation are not exposed. Every action requires a fresh snapshot and
element token. An unknown action outcome is never retried.

### Development and release provisioning

```bash
cd desktop
npm run provision:driver     # pinned version from package.json, SHA-256 verified
npm run pack
```

The verified executable and its local integrity stamps are ignored build
artifacts. The release workflow provisions independently on macOS, Windows, and
Linux. On macOS the nested executable must be signed before the enclosing
Integral application is signed and notarized. Release CI fails closed unless
`MAC_CSC_LINK`, `MAC_CSC_KEY_PASSWORD`, `APPLE_ID`,
`APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID` are configured.

The desktop package currently disables asar because Cua's pinned native SDK
resolves its dynamic library by physical filesystem path. A packaged smoke test
must import `@trycua/cua-driver` before a release is published; putting the SDK
back inside `app.asar` breaks native loading even when electron-builder unpacks
the library beside it.

Implementation: `src/computer-use-broker.js` owns bounded worker lifecycle,
manifest generation, exact-window reads, artifact limits, and generation
identity. `src/environment-host.js` owns the narrow remote RPC and binary
artifact framing. `src/driver-install.js` is release provisioning only and is
never callable from the renderer or at runtime.

## Pointing at a backend (packaged / `npm start` mode)

`npm start` (and packaged builds) load the bundled renderer from `file://`,
which has no same-origin backend — the shell injects one via the preload
bridge. Resolution order, first non-empty wins:

1. `--api-url=<url>` CLI flag / `INTEGRAL_API_URL` env var
2. `settings.json` in the app's userData dir (`{ "apiUrl": "..." }`)
3. `http://localhost:4000`

```bash
INTEGRAL_API_URL=https://integral.example.com npm start
npm start -- --api-url=https://integral.example.com
```

In-app override (no restart of the shell, reloads the page): from the
DevTools console (or a future settings UI via the same bridge),

```js
localStorage.setItem('integral.api_url', 'https://integral.example.com');
location.reload();
```

to clear: `localStorage.removeItem('integral.api_url')`. The bridge
(`window.integralDesktop.setApiUrl`) persists to `settings.json` instead.

macOS **Accessibility** and **Screen Recording** still require a human
toggle. Bundling does not skip TCC; it only chooses which signed process
owns it. The active path is `CuaDriver.createPrivateWorker()` with a
nested `cua-driver` binary, so those grants attach to **Integral** (or
**Electron** under `npm run dev`), not to a separately installed
`CuaDriver.app`. ADR-013's early S1/S1b note (PATH `cua-driver mcp` →
vendor app so TCC stays on `com.trycua.driver`) is the documented
compatibility alternative, not the shipped implementation — see
[the Cua integration review](../docs/reviews/2026-09-cua-remote-desktop-integration-review.md).

Local unpackaged runs still need the nested binary on disk:

```bash
cd desktop && npm run provision:driver
```

That is release-style provisioning into `resources/driver/`, not the
vendor installer. `driver-install.js` is not callable at runtime.

The in-app **Allow** dialog for a computer-use lease *is* skipped when the
backend is loopback **and** you launched with `npm run dev`
(`--dev` / `INTEGRAL_DESKTOP_DEV=1`), or when
`INTEGRAL_DESKTOP_SKIP_CUA_CONSENT=1` is set for a localhost `npm start`.
OS permission checks still run.

## Packaging

```bash
npm run dist     # frontend build + electron-builder installer (desktop/release/)
npm run pack     # unpacked dir build for a quick smoke test
```

`npm run build:renderer` rebuilds `../frontend/dist` and copies it to
`desktop/renderer/` (gitignored build output). The installer targets are
dmg/zip (mac), nsis/portable (win), AppImage/deb (linux).

## Releasing

`desktop/VERSION` is the release version and must contain a semantic version
such as `1.2.3` or `1.2.3-beta.1`. Change only that file when cutting a new
desktop release. After the change reaches `main`, the `Release desktop`
workflow builds the Linux, macOS, and Windows installers and publishes them in
a GitHub release tagged `desktop-v<version>`.

The workflow applies the version to the packaged app at build time, so
`package.json` and `package-lock.json` do not need a release-only version bump.
Reusing an existing release version is rejected rather than overwriting its
tag or assets. Versions with a prerelease suffix create GitHub prereleases.

## Window style

On macOS the shell uses a hidden title bar — traffic lights float over the
content, ChatGPT-app style, with no title text. The sidebar (and the auth
screens' editorial column) double as the window drag surface; every button,
link and input opts out via `-webkit-app-region: no-drag`, and the sidebar
logo row drops below the traffic lights. The transparent TopBar overlay is
deliberately NOT a drag zone (its empty middle stays click-through to page
content). Windows/Linux keep the native frame with an auto-hidden menu.

macOS builds also install an Integral mark in the system menu bar. Its native
quick-access menu lists the ten most recent conversations and can reveal the
app, start a fresh chat, open notifications or settings, and quit without first
finding the app window. The mark is a macOS template image, so it follows light
and dark menu-bar appearances automatically.

The desktop bridge also mirrors newly arriving Integral notifications to the
operating system's native notification center. The first sync establishes a
baseline instead of replaying historical unread items; later notifications
display natively, update the macOS Dock badge, and open their Integral target
when clicked.

## How the web app adapts

No fork — the same `frontend/` sources detect the shell
(`frontend/src/config.ts: isDesktop()` / preload bridge):

- `getBackendOrigin()` prefers the bridge value over same-origin `/api`,
  so REST (`api/client.ts` re-resolves per request), raw `fetch()` paths
  (`api/sharing.ts`, `SharedPortfolioPage`) and SSE (`JvAgentProvider`)
  all hit the configured backend.
- `getWebSocketUrl()` builds absolute `ws(s)://` URLs for
  `/api/events` (`api/events.ts`) and `/ws/agent-events`
  (`hooks/useAgentiveWebSocket.ts`); browsers keep same-origin behavior.
- `main.tsx` mounts a `HashRouter` instead of `BrowserRouter` under
  `file://`, where there is no server fallback for deep links/reloads.
