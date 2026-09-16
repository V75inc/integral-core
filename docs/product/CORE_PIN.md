# Core pin runbook (commercial → open integral-core)

Commercial Integral depends on open **integral-core** by **version pin**, not
by forking Core files.

## Pin forms (v0.1)

1. **Git tag (uv / pip)** — preferred until a PyPI / TestPyPI pin is cut
   (see `RELEASING.md`: `rcN` → TestPyPI, final → PyPI):

   ```toml
   # backend/pyproject.toml (commercial)
   [tool.uv.sources]
   integral-core = { git = "https://github.com/V75inc/integral-core", tag = "v0.1.0" }
   ```

2. **Docker** — product image `FROM` Core image, then layer `packages/apps`:

   ```dockerfile
   FROM ghcr.io/v75inc/integral-core:0.1.0 AS core
   # COPY packages/apps + set INTEGRAL_PACKAGE_PATHS=/app/packages/apps
   ```

## Bump procedure

1. Land change in public `integral-core` → CI green (`verify-core-only` + contract).
2. Tag `v0.x.y` (semver; breaking Core API → major).
3. Commercial PR: bump pin only; run product smoke with `packages/apps` on path.
4. Never edit Core sources inside the commercial tree to “make Apps work” —
   open a Core PR instead.

## Rules

- One-way dependency: commercial → Core.
- Apps reach Core only via extension contract / `ToolContext` /
  `INTEGRAL_PACKAGE_PATHS`.
- Hotfixes land in Core first; commercial only bumps the pin.
