# Session renewal

The inactivity dialog belongs only to an authenticated, server-verified account
on a private page whose access token has not expired. A cached profile alone
cannot enable it. Login, signup, password recovery, verification and public share
pages never show it.

## User flow

- Human input updates the shared activity timestamp. Polling, token refresh and
  making a tab visible do not count as human input. Recent activity permits
  silent refresh, bounded by the issued access-token lifetime and deployment
  activity window (default 900 seconds).
- With a nonzero `idle_timeout_seconds`, an inactive but valid session pauses
  behind **Are you still working?** The mounted page and its inputs stay in
  memory, with the background inert.
- **Continue working** verifies the same account through `/auth/me` before
  unlocking. Refresh credentials are exchanged when available; an access-only
  session can resume while its access token remains valid.
- Temporary renewal failures remain retryable while the original session is
  valid. Access expiry and authoritative authentication rejection end the local
  session and return to login, including when the inactivity dialog is open.
  They never create an inactivity prompt or a password dialog over login.
- **Sign out** immediately retires local tokens, timers, recovery state, query
  caches and chat sessions. Server revocation uses the captured token and cannot
  clear a newer login. Explicit sign-out/account changes in another tab also
  retire the old page.

## Security and delivery boundaries

This is a client-side usability lock, not a replacement for server access,
refresh, revocation, permission or expiry checks. It does not extend token
lifetimes or change the server's authentication policy. New Axios requests are
cancelled while paused; raw chat sends/reconnections follow the same lock.
An already accepted server operation may still complete. Renewal never resends
an interrupted mutation or chat turn; the user must check its status first.

Renewal requests bypass the paused application client, have finite timeouts,
verify `/auth/me` before installing credentials, and reject responses from a
retired session. Successful renewal invalidates read queries to reconcile
server state. Draft retention is in memory in the same tab; closing/reloading
the tab, signing out, or an editor's own data-reset behavior can discard inputs.
This feature does not introduce persistent draft storage.

The desktop host uses its existing session provider rather than prompting for
a hosted-account password. Expired sessions, cold unauthenticated visits and public auth routes use the
ordinary sign-in flow.

Cross-origin deployments require CORS headers on authentication rejections so
the browser can expose the real HTTP status (Core PR #155).

## Validation

Automated regressions cover public pages with stale credentials, unverified
cached profiles, idle/expiry ordering, expiry while locked, tab visibility,
access-only renewal, cancelled mutations, foreign-account rejection, logout
while requests are pending, and late refresh/profile responses.

The 2026-10-19 browser qualification exercised the previous expired-session
password-recovery behavior. That flow has been superseded by the private-page,
valid-session inactivity contract above; those historical results do not qualify
the revised expiry behavior. Production deployment remains a separate action.

## Local desktop runtime

The trusted deployment descriptor selects local installation-owner identity.
Before metadata is reachable, an installation-owner host adapter supplies the
same conservative desktop behavior. Explicit hosted metadata takes precedence;
merely having an accounts host or a refresh token does not select desktop mode.

Local sessions never use the inactivity/password dialog or expiry redirects.
Core requests a fresh, finite-lived token through `HostSessionAdapter.session()`
regardless of human activity, before expiry, after wake, and after a rejected
bearer. Concurrent callers share one exchange. Core verifies `/auth/me` and the
retained owner identity before installing the token. All API authorization,
installation-controller checks and native host restrictions still apply.

A temporary runtime failure leaves the mounted page, drafts, workspace scope
and cached account intact. Expired tokens are withheld and pending new work is
cancelled before transport. A persistent reconnecting/recovery banner offers a
retry; background retries back off from five to thirty seconds. Recovery does
not replay a mutation or chat submission. A changed owner requires attention
and cannot unlock the old owner's retained page. Explicit sign-out still clears
local state, cancels retries and fences late responses.

Core owns this lifecycle and its neutral host contract. The desktop distribution
owns the native IPC bridge, installation credential, account/offline experience
and runtime startup. Central-account availability is not a renewal prerequisite.
