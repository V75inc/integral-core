# Session policy

Set these variables on the API container for each deployment. The Compose and
Swarm templates pass them through; all environment example files list them.
Restart the API and reload open browser tabs after changing the policy. Browser
policy is loaded from public `/meta/deployment` metadata at startup, so no frontend
rebuild is required. Metadata contains timing values only, never credentials.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JVSPATIAL_JWT_EXPIRE_MINUTES` | `60` | Server-enforced access-token lifetime in minutes (previously 30). |
| `JVSPATIAL_REFRESH_EXPIRE_DAYS` | `7` | Refresh-token lifetime in days. |
| `JVSPATIAL_REFRESH_TOKEN_ROTATION` | `true` | Rotate refresh tokens on renewal. |
| `INTEGRAL_SESSION_IDLE_TIMEOUT_SECONDS` | `0` | Optional browser idle lock; 0 disables it. |
| `INTEGRAL_SESSION_SILENT_REFRESH_ACTIVITY_SECONDS` | `900` | Human activity window allowing silent renewal (previously 300 seconds). 0 requires explicit renewal. |
| `INTEGRAL_SESSION_REFRESH_LEAD_SECONDS` | `120` | Preferred renewal lead before access expiry. |
| `INTEGRAL_SESSION_MIN_REFRESH_INTERVAL_SECONDS` | `30` | Preferred minimum renewal interval, bounded by the issued expiry. |
| `INTEGRAL_SESSION_RENEWAL_REQUEST_TIMEOUT_SECONDS` | `20` | Timeout for built-in login, refresh and identity confirmation requests. |

For example, a deployment can set `INTEGRAL_SESSION_IDLE_TIMEOUT_SECONDS=3600`
to lock its browser view after one hour without human activity. The default
leaves this additional lock disabled: an inactive tab pauses when its access token
expires, while an active tab can renew silently. Polling and background renewal
do not count as human activity. Activity timestamps are shared across tabs.

The idle lock preserves the mounted workspace and drafts behind an inert, blurred
view and offers explicit renewal or sign-in. It is a browser UX control, not
server-side inactivity revocation. Actual token validity and authorization remain
enforced by the API; the browser follows the issued `expires_in`, including tokens
issued before a configuration change. Existing tokens keep their original expiry.

Refresh rotation can issue a fresh refresh lifetime. Seven days is therefore not
an absolute maximum login age. This policy does not introduce an absolute session
age limit. Host identity adapters continue to own their authentication behavior.

The browser uses compatible defaults if public policy metadata is unavailable or
an older API omits it. Invalid timing fields fall back individually to defaults.
