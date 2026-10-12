# Changelog

This pre-launch change record describes the supported Integral Core runtime.
Package publication and deployment are separate release gates; a local version
bump does not establish that a release is available on a package index.

## Unreleased

- Continue safely after terminal failed or cancelled assistant runs when scoped
  receipts permit it; preserve uncertain provider usage and unresolved-write
  fences. Close provider streams in their owning task during worker interruption.
- Preserve App editor controls when non-admin access responses hide the
  collaborator directory, using the caller's current server-resolved role.
- Store per-user IANA time zones, infer unset zones from the authenticated browser,
  and expose an editable Profile setting. New reminders and relative-date query
  plans inherit the account zone while existing and approved schedules retain
  their saved zone; ambiguous local one-shots require an explicit offset.
- Pin jvspatial 0.2.1. Routine database timings use DEBUG, keeping INFO logs quiet
  while slow-query/failure warnings and operation metrics remain available.

## 0.1.1rc17 — 2026-10-12

- Typed relation writers require current target-read authority and canonical
  Entry/Track/App containment. Declared cross-App targets resolve through the
  existing workspace and instance contract, and verified owning App IDs are
  stored on reference edges. Denials preserve the source record and links;
  delegated provenance does not supply independent authorization.
- Establish governed access-control lifecycle checks and preserve authorization
  fences across staged operations.
- Return App and Track nodes to the caller's active graph context after creation
  so subsequent writes remain usable and inside the enclosing transaction.

## 0.1.1rc16 — 2026-10-09

- Integral AI, powered by Pydantic AI, is the sole resident harness and is
  always active after installation. No activation flag or harness selector is
  required. Saved provider choices cannot replace the resident.
- Model setup uses Settings → AI Models. A missing model produces actionable
  setup guidance while Integral AI remains active.
- Model configuration exposes one primary chat model and optional voice input;
  obsolete harness slots and JVAgent compatibility are removed.
- Native discovery returns bounded model overviews and precise capability
  results before loading full definitions. Rejected tool calls retain structured
  refusal codes, and interrupted budget failures preserve completed effects.
- Core requires Python 3.11 or newer. Managed installations isolate their
  settings and enforce authentication, credential encryption and rate limits.
- Core installs as one wheel with ordinary PyPI runtime dependencies. Portable
  resident skills ship with the wheel and use the same instructions as a source
  checkout.
- Managed local startup reports the actual resident identity. A previously
  running installation must be restarted before it can be reused by the desktop
  launcher.
- Business's desktop launcher consumes the public Core startup contract and
  bundles Core without a separate agent runtime.
- Packaged HTML is not cached across upgrades. Business clears its HTTP cache
  before opening the local workspace so a prior interface cannot mask the
  installed resident or reference removed assets.
