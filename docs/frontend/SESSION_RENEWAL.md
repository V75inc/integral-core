# Session renewal

An expired session pauses an open authenticated page behind a native modal.
The page stays mounted so its local inputs are retained in the current tab.
The backdrop hides private content and the browser makes the rest of the page,
including portalled editors and the chat dock, inert.

## User flow

- Recent human activity allows the existing silent refresh to run. Background
  polling is not activity. The silent refresh window is at most five minutes,
  bounded by the issued access-token lifetime.
- At access expiry, the user chooses **Continue working**. The server must
  accept the refresh token and return the same account's profile before the
  page unlocks.
- If refresh is rejected, the prompt asks for the current account's password.
  Credentials use the existing login endpoint. Only the same account can
  restore the retained page. Changing accounts requires signing out.
- Network errors, server failures and rate limits leave renewal retryable.
  Passwords are cleared after each attempt and never persisted by the form.
- **Sign out** discards local session state, query caches and chat sessions.
  Explicit sign-out/account changes in another tab also retire the old page.

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
a hosted-account password. Cold unauthenticated visits and public auth routes
continue to use the ordinary sign-in flow.

Cross-origin deployments require CORS headers on authentication rejections so
the browser can expose the real HTTP status (Core PR #155).
