# Changelog

This pre-launch change record describes the supported Integral Core runtime.
Package publication and deployment are separate release gates; a local version
bump does not establish that a release is available on a package index.

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
