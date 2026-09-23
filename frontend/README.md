# Integral frontend

React 18 + TypeScript + Vite + Tailwind CSS. Renders the Integral knowledge platform UI against the FastAPI backend at `http://localhost:4000`.

See root [README.md](../README.md) for product overview, [docs/README.md](../docs/README.md) for technical docs, and [AGENTS.md](../AGENTS.md) for repo conventions.

---

## Quick Start

### Prerequisites

- **Node.js** 18+
- **npm** (or pnpm/yarn — only npm is tested in CI)
- Integral backend running on `http://localhost:4000` (see [backend/README.md](../backend/README.md))

### Setup

```bash
cd frontend
npm install
npm run dev        # http://localhost:9006
```

Vite proxies `/api/*` to `VITE_BACKEND_URL` (default `http://localhost:4000`).

### Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Vite dev server on `:9006`, host `0.0.0.0`, HMR |
| `npm run build` | `tsc` + `vite build` → `dist/` |
| `npm run preview` | Serve built artifacts on `:9006` |
| `npm run test` | Vitest watch mode |
| `npm run test:run` | Vitest single pass (CI) |
| `npm run lint:types` | `tsc --noEmit` |

---

## Project Structure

```
frontend/
├── src/
│   ├── api/                  axios client + per-resource modules
│   │                         (auth, apps, tracks, entries, sharing, …)
│   ├── components/
│   │   ├── ui/               primitives — Button, Toast, Modal, FieldRenderer, …
│   │   ├── entries/          composer + detail + field-type registry
│   │   │   └── fieldTypes/   text, number, boolean, date, markdown,
│   │   │                     select, multi_select, relation, computed,
│   │   │                     file, files (auto-registered)
│   │   ├── tracks/           TrackModal, TrackHeader, PinButton
│   │   ├── apps/             AppModal, AppHeader, PinButton
│   │   ├── views/            widget registry + composable meta-widgets
│   │   │   ├── composable/   composable_list / _grid / _board / _timeline
│   │   │   ├── plugins/      auto.ts plugin loader
│   │   │   └── registry.tsx  central view-type registry
│   │   ├── layout/           Sidebar, WorkspaceSwitcher, EmailVerificationBanner
│   │   ├── collab/           UserSearchPicker, share dialogs
│   │   ├── system/           SystemNotificationBar + provider +
│   │   │                     apiErrorNotifier + useOfflineNotification
│   │   ├── sidebar/          nav rail, pinned items
│   │   ├── chat/             AI chat composer, message renderers
│   │   ├── feed/             FeedFilterStrip, FeedCard, retrieval search
│   │   ├── notifications/    NotificationCard (structured Actor: rest)
│   │   ├── settings/         settings section host
│   │   ├── command/          ⌘K palette (Workspaces/Pinned/Apps/Tracks/Entries/Actions)
│   │   ├── activity/         activity feed widgets
│   │   ├── approvals/        Policy approval surface
│   │   ├── mentions/         @mention picker + chips
│   │   ├── library/          OperationalModel library browser
│   │   ├── workspace/        WorkspaceCard, member rows
│   │   └── onboarding/       first-run flows
│   ├── pages/                route-level pages
│   │                         (MissionControl, Apps, Tracks, Feed, Settings,
│   │                          AIChat, OperationalModels, Workspaces, Login,
│   │                          Signup, ForgotPassword, ResetPassword,
│   │                          VerifyEmail, InvitationAccept, …)
│   ├── features/
│   │   ├── ai-chat/          assistant-ui Thread + bridges
│   │   └── settings/         settings sections — Profile, Policies, Search, …
│   ├── hooks/                useScope, useWorkspace, useDebounce, …
│   ├── context/              AuthContext, ScopeContext, ToastContext,
│   │                         SystemNotificationsContext
│   ├── utils/                authValidation, humanizeFieldKey, formatters
│   ├── lib/                  operational-model manifest, telemetry
│   ├── brand.ts              brand strings + tagline
│   ├── App.tsx               route table + provider mount order
│   └── main.tsx              entrypoint — BrowserRouter v6 (v7 future flags)
├── public/
├── package.json
├── vite.config.ts            proxy /api → VITE_BACKEND_URL
├── vitest.config.ts
├── tsconfig.json
├── tailwind.config.js
└── postcss.config.js
```

---

## Tech Stack

| Layer | Choice |
|---|---|
| Framework | React 18 |
| Language | TypeScript (strict) |
| Build | Vite |
| Routing | React Router v6 with `v7_startTransition` + `v7_relativeSplatPath` future flags |
| Styling | Tailwind CSS |
| Data | TanStack Query (server state) + Context (auth, scope, toast, system notifs) |
| HTTP | axios (with workspace-scope header injection + system-notif error routing) |
| AI chat | `@assistant-ui/react` |
| Drag & drop | `@dnd-kit/core` + `@dnd-kit/sortable` |
| Doc rendering | `mammoth` (docx), `pdfjs-dist` (pdf preview) |
| Icons | `lucide-react` |
| Date | `date-fns` |
| Test | Vitest + React Testing Library |

---

## Workspace Scope (`X-Integral-Scope`)

The backend enforces workspace isolation via a request header. Every list endpoint requires `X-Integral-Scope: <workspace_id>` and refuses cross-workspace reads.

**Frontend wiring:**

- `ScopeContext` (`src/context/ScopeContext.tsx`) — holds the active workspace id, persists to localStorage, syncs with `WorkspaceSwitcher`
- `src/api/client.ts` — axios request interceptor injects the header from `ScopeContext` on every request
- Resource pages auto-switch the active workspace when opening a resource in another accessible workspace (e.g. clicking a Track that belongs to a workspace different from the one currently active)

**Agent surface:**

The AI chat passes the active workspace id into agent tool calls via the `current_scope_workspace_id` backend ContextVar (set by the bridge per turn). This drives the two-layer workspace gate on `activity_digest`, `query_entries`, `count_entries_grouped`, `resolve_entry`, and profile tools. See [backend/README.md](../backend/README.md) → "Agent two-layer workspace scope."

---

## System Notification Bar

App-wide priority-queued surface for conditions that warrant more prominence than a toast: email verification, server-unreachable, offline, scheduled maintenance, etc.

Lives at `src/components/system/`.

| File | Role |
|---|---|
| `SystemNotificationsContext.tsx` | React context + priority queue (error 100 > warning 75 > info 50 > success 25; caller may override). Exposes a module-level `_liveApi` slot so non-React modules (axios interceptor, error boundaries) can push without a hook. |
| `SystemNotificationBar.tsx` | HelloBar-style fixed-top renderer (`z-1000`). 500ms entrance delay, slide-down via `.system-bar-animated`. Reports its height onto `:root` as `--system-bar-h` via `ResizeObserver` so the layout pads in sync. |
| `apiErrorNotifier.ts` | `notifyApiFailure(err, {context, onRetry})` / `clearApiFailure()`. Classifies network/5xx/4xx/408/429; skips 401/403. |
| `useOfflineNotification.ts` | Watches `navigator.onLine`, pushes a persistent `system:offline` (priority 200, non-dismissible). |

**Mount order (already wired in `App.tsx`):**

```tsx
<SystemNotificationsProvider>
  <SystemConnectivityWatchers />
  <SystemNotificationBar />
  <div className="system-bar-layout" style={{ paddingTop: 'var(--system-bar-h, 0px)' }}>
    <Suspense ...><Routes>...</Routes></Suspense>
  </div>
</SystemNotificationsProvider>
```

The padding wrapper sits OUTSIDE the auth/public route split so public surfaces (login, signup, forgot-password, reset-password, verify-email, invitation accept) squeeze down too. `Sidebar` adopts `.system-bar-sidebar` so its `top`/`height` animate with the same `380ms cubic-bezier(0.16, 1, 0.3, 1)` easing as `.system-bar-layout`. Chat pages compute their viewport as `calc(100vh - var(--system-bar-h, 0px))`.

**Conventions:**

- **Global API failures auto-route.** `src/api/client.ts` calls `notifyApiFailure(err)` on every non-401 error and `clearApiFailure()` on the next successful response. Per-request opt-out: `config.__suppressSystemNotify = true`. Routes that own their own error UI (auth flows) are listed in `SUPPRESS_NOTIFY_PATHS`.
- **Page-level retry.** Pages that previously rendered inline "Could not load X" banners now react to query error inside `useEffect` and call `notifyApiFailure(err, { context, onRetry })` — the axios interceptor already pushed a generic notification; this *upgrades* it with context + a Retry action. Reference: `pages/FeedPage.tsx`, `pages/MissionControlPage.tsx`.
- **Programmatic feature banners.** Components owning a condition (e.g. `EmailVerificationBanner`) call `notify({ id, type, icon, title, actions })` and MUST `dismiss(id)` on unmount.

Add a new system condition by giving it a stable `id` (`system:rate-limited`, `auth:email-verification`, …), choosing a `type` (priority follows), and pushing via `useSystemNotifications()` or `getSystemNotificationsApi()` outside React.

---

## Field Type & View Type Registries

Mirror the backend OperationalModel substrate — extensible at runtime.

**Field types** (`src/components/entries/fieldTypes/registry.ts`):

`text | number | boolean | date | datetime | markdown | json | select | multi_select | relation | computed | file | files`

Each registers a renderer + an editor. `FieldRenderer.tsx` is the central display gateway and applies type-aware heuristics:

- **Currency formatting** for keys matching `amount`, `value`, `price`, `cost`, `revenue`, `arr`, `mrr`, `acv`
- **Percent formatting** for keys ending `_pct`, `_percent`, or named `probability`
- **Humanized field labels** via `utils/humanizeFieldKey.ts` — strips `_track` suffix, hides `_`-prefixed internal keys, Title-Cases the rest
- **Title-Cased enum values** for `select` / `multi_select` editors

**View palette** (`src/views/` — profiles reference keys, clients render):

- **Contracts (synced):** `src/views/contracts.json` from `backend/app/views/contracts/` (`npm run sync:view-contracts`)
- **Registry:** `src/views/registry.tsx` — `registerWidget()`, `ViewRenderer`, `MissingWidget`
- **Manifests:** `src/views/manifests/*.manifest.ts` auto-discovered at boot (drop-in registration)
- **Widget components:** `src/components/views/` (implementations; composable meta-widgets in `composable/`)

Palette keys: `feed | kanban | table | calendar | gallery` + composable
`composable_list | composable_grid | composable_board | composable_timeline`.

`src/views/plugins/auto.ts` fetches substrate on boot for `MissingWidget` hints.
Convention: [../docs/operational-models/VIEW_PALETTE.md](../docs/operational-models/VIEW_PALETTE.md).

---

## Auth Flow

- **Login** (`/login`) — JWT (access + refresh) via jvspatial auth
- **Signup** (`/signup`) — inline validation + password-strength meter (`utils/authValidation.ts`)
- **Forgot password** (`/forgot-password`) — sends reset link
- **Reset password** (`/reset-password?token=…`) — token-validated reset
- **Verify email** (`/verify-email`) — 6-digit OTP entry; non-blocking (login never gated)
- **Invitation accept** (`/invite/:token`) — workspace/resource invitation redemption

System notification bar surfaces unverified-email state via `EmailVerificationBanner` so users can resend / verify without leaving their current page.

---

## Settings

Sections live under `src/features/settings/sections/`:

- **Profile** — avatar upload, display name (via `/auth/update-profile`), read-only email, password-reset shortcut
- **Policies** — policy management (requires-human-approval gating)
- **Search** — retrieval config
- **Notifications** — per-channel preferences
- *(more sections added by registering in `pages/SettingsPage.tsx`)*

---

## Routing

React Router v6 with v7 future flags enabled in `main.tsx`:

```tsx
<BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
```

Route table in `App.tsx`. Suspense boundary wraps lazy-loaded pages.

**Retired routes:**

- `/shared` — redirects to `/`. Shared resources surface in Mission Control. Backend `/me/shared` + `/me/invitations` aggregators remain (consumed by Mission Control + invitation accept).

---

## Testing

```bash
npm run test:run        # Vitest single pass — CI
npm run test            # Vitest watch
npm run lint:types      # tsc --noEmit
```

**Patterns:**

- Vitest + `@testing-library/react`
- Component tests colocated with the component (`Foo.test.tsx` next to `Foo.tsx`) or under `__tests__/`
- `?raw` Vite imports for source-level guard tests (e.g. asserting `defaultOpen={false}` literal in `Disclosure.tsx`)
- Avoid `Array.prototype.at(-1)` — tsconfig target is ES2020. Use `arr[arr.length - 1]`.

---

## Environment

Frontend reads only `VITE_BACKEND_URL` (default `http://localhost:4000`). All other config flows through the backend.

Vite's proxy intercepts `/api/*` requests in dev. In production, serve `dist/` behind a reverse proxy that routes `/api/*` → backend.

---

## Conventions

- **TypeScript strict mode** — no implicit any
- **Tailwind for styling** — minimize bespoke CSS; reuse design tokens
- **TanStack Query for server state** — never store server responses in Context
- **Context for cross-cutting concerns** — auth, active workspace scope, toast, system notifications
- **axios via the shared client** (`src/api/client.ts`) — never `fetch` directly; this preserves scope-header injection and system-notif error routing
- **Errors** — server JSON shape is `{error_code, message, details, timestamp, path}` (Pydantic 422 paths still return `{detail: ...}`); the shared client normalizes both before throwing
- **No emojis in source files** unless explicitly requested

See [AGENTS.md](../AGENTS.md) for the full repo convention set.

---

## Build

```bash
npm run build           # tsc + vite build → dist/
npm run preview         # local preview of dist/ on :9006
```

`vite build` runs `tsc` first (type-check gate). Build fails if there are type errors.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `404` on `/api/*` in dev | Backend not running on `:4000`, or `VITE_BACKEND_URL` mismatch |
| Cross-workspace data leak | Missing `X-Integral-Scope` header — check axios interceptor wiring + `ScopeContext` value |
| `useLayoutEffect` warning during SSR-style tests | Wrap render in `act()` or use `useEffect` if the seed doesn't need to win a paint race |
| Composer loses first keystroke | Regression of B-ENT-01 — `EntryFormExpanded` `initialTitle` seed must use `useLayoutEffect`, not `useEffect` |
| `Array.at is not a function` in build | tsconfig target is ES2020; replace `arr.at(-1)` with `arr[arr.length - 1]` |
| System notification bar doesn't animate | `--system-bar-h` not propagating; check `ResizeObserver` wiring in `SystemNotificationBar.tsx` |

---

## License

MIT — see root `LICENSE`.
