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

## Packaging

```bash
npm run dist     # frontend build + electron-builder installer (desktop/release/)
npm run pack     # unpacked dir build for a quick smoke test
```

`npm run build:renderer` rebuilds `../frontend/dist` and copies it to
`desktop/renderer/` (gitignored build output). The installer targets are
dmg/zip (mac), nsis/portable (win), AppImage/deb (linux).

## Window style

On macOS the shell uses a hidden title bar — traffic lights float over the
content, ChatGPT-app style, with no title text. The sidebar (and the auth
screens' editorial column) double as the window drag surface; every button,
link and input opts out via `-webkit-app-region: no-drag`, and the sidebar
logo row drops below the traffic lights. The transparent TopBar overlay is
deliberately NOT a drag zone (its empty middle stays click-through to page
content). Windows/Linux keep the native frame with an auto-hidden menu.

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
