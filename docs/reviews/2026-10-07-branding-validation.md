# Integral logo integration — validation record

Date: 7 October 2026. Checkout: `codex/pr-113-staging`, based on `da059fbf`.

## Implemented

- Current approved frameless split-square master from `docs/branding/logo-exploration-2026-10-06/`. The artwork was updated during integration; the earlier O16 master is retained as provenance rather than used in the application.
- Shared `Logo` / `LogoMark` geometry for sidebar, authentication, loading and callback surfaces, and agent activity. Integral AI's native avatar uses the same mark; explicit custom avatars remain intact.
- Black light-theme and white dark-theme marks; transparent diagonal gap; block-level SVG alignment to avoid inline baseline drift.
- Authentication backdrop waves use the current two-half vector geometry, with no old surrounding frame.
- SVG favicons for browser color schemes, ICO fallback, PNG favicon, Apple touch icon, Android icons and web manifest. Versioned icon references invalidate the old artwork cache.
- Regression tests compare application and authentication-wave geometry directly with the public SVG master, and verify native versus custom avatar behavior.

The concurrent changes to authentication presentation and artwork were preserved. Pre-existing staged stream-recovery work was not modified or unstaged.

## Automated checks

| Check | Result |
|---|---|
| `make verify` | Passed; guards, pinned formatting, frontend types/lint, CI reproduction and available full suites completed. |
| Frontend full suite inside that gate | 252 files, 1,454 tests passed. |
| Final focused logo/auth/avatar tests | 3 files, 11 tests passed. |
| `make verify-core-only` | Passed; Core-only boot/contracts and extension-boundary guards completed. |
| Frontend production build | Passed. Existing large-chunk warnings remain. |
| Clean Docker frontend build and deployment | Passed for project `integral-pr113`, serving port 9107. |
| `git diff --check` | Passed. |

The broad test gate skipped PostgreSQL-only and environment-dependent integration cases according to their markers. It is not evidence that those scenarios passed. No connector multi-tenant fault-injection qualification was performed in this branding round.

## In-browser checks

The deployed frontend was inspected at `http://localhost:9107/agent` and `/login` in the in-app browser. Dark/light variants, collapsed/expanded sidebar marks, Integral AI avatar, authentication backdrop and activity marks all showed the same current geometry. The existing conversation restored after reload.

A real model turn used ordinary language: **“Who is in my customer list?”** It returned the one saved customer, Ana; showed the tool trace, settled, cleared the composer and scrolled to the answer. This used the existing workspace and long conversation, not a clean workspace.

The answer's Ana link opened the actual saved entry. Its **Close** control dismissed the dialog and removed the `entry` query parameter without using browser history. Navigation back to the full chat retained the conversation.

The correct answer still required 4 model requests, 114,031 input tokens and 362 output tokens. The first tool attempt failed because a remembered deferred tool was not yet available; capability search and the subsequent query recovered. This is recorded as R6 in the engineering review, not represented as a fully qualified efficient harness. Cost was unavailable, not zero.

| Screenshot | Purpose |
|---|---|
| `assets/2026-10-07-branding/chat-result-dark.jpg` | Final deployed sidebar, native avatar, activity mark and lookup result. |
| `assets/2026-10-07-branding/chat-light.jpg` | Same conversation in light theme. |
| `assets/2026-10-07-branding/login-dark.jpg` | Current mark and authentication backdrop in dark theme. |
| `assets/2026-10-07-branding/login-light.jpg` | Current mark and authentication backdrop in light theme. |
| `assets/2026-10-07-branding/chat-working-dark.jpg` | Current logo during an actual working turn. |

No additional customer records, connector bindings, credentials or permissions were changed. Theme/sidebar test preferences were restored. Changes and review artifacts remain in the working tree; no commit or push was performed in this round.
