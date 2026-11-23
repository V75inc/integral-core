# Temporary Safari Web Chat fixture

This is a local, synthetic qualification, not a production deployment or
standard Integral startup path. The real built visitor frontend uses the
connector's actual cookie, approval, routing and turn path with a synthetic
profile/model and throwaway PostgreSQL. Staff management remains in-process;
existing Integral services and data are not consulted.

## Get explicit approval first

The operator must agree to both temporary changes:

1. Append one tagged `127.0.0.1 integral-webchat.test` line to the Mac's
   `/etc/hosts`. Omit IPv6 and never replace an existing mapping.
2. Add only the fresh one-day synthetic server leaf to the current user's
   login Keychain, for SSL and precisely `integral-webchat.test`.
   The leaf is non-CA, has a single DNS SAN and has only server-auth usage.

The hosts change requires local administrator authorization. From an
interactive terminal, the owner can run `sudo -v` themselves, then launch
the script from that same terminal. The script never asks for or receives an
administrator password and its elevated commands use `sudo -n`. Do not
assume the timestamp transfers to an assistant's separate terminal. macOS
may separately require owner confirmation for the user trust change.

## Run when ready to test

Use the disposable checkout, with reviewed Core test dependencies, frontend
dependencies, PostgreSQL utilities, and OpenSSL:

```sh
bash backend/scripts/run_safari_disposable_web_chat.sh
```

The script refuses an existing test mapping, matching certificate, unexpected
default Keychain, or either port already in use. It verifies regular macOS SSL
trust for the exact name without supplying an explicit anchor, and refuses if
a different name validates. The runner prints a disposable visitor URL.
Open it in Safari and send a synthetic test message. If Safari shows a trust
warning, stop; do not override it or broaden trust. Chromium's Mac verifier
does not honor this hostname-specific Keychain policy constraint. No browser
flags, settings, or URLs outside the fixture are part of this procedure.

## Shutdown and verification

Ctrl-C stops early. The hosts supervisor has a 15-minute hard cap and retains
privileges to remove only its tagged line even if the sudo timestamp expires.
The fixture's requested serving time is shorter to leave time for build and
teardown. Normal exit, early failure and catchable signals run exact cleanup.
The script removes trust and its login-Keychain certificate by SHA-256, deletes
temporary TLS files and verifies both loopback ports are closed. The underlying
runner removes its disposable PostgreSQL on exit. Do not force-kill either
supervisor; a forced kill, machine shutdown or denied cleanup can require
manual verification.

If the script reports incomplete cleanup, use its exact hostname/tag to
remove only its tagged line, and its exact SHA-256 to remove that certificate
and associated user trust from the same login Keychain. Do not clear personal
trust or replace the entire hosts file. The private key is not retained on
reported partial cleanup. Confirm that the hostname no longer resolves to
loopback, the leaf is no longer trusted, and ports 58173/55523 are unused.
