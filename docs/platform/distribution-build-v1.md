# Distribution composition contract v1

Core owns the neutral frontend and runtime/database launcher. Integral One owns
the reusable Apache `packages/desktop-host` package and native desktop product. Product repositories compose these artifacts rather
than copying an independently maintained editor or agent frontend.

## Source and UI builds

From Core, `python3 scripts/distribution.py snapshot --output <fresh-directory>`
emits the frontend, preserved LICENSE/NOTICE, the composition utility and a
per-file SHA-256 inventory with a tree identity. The identity includes uncommitted
source bytes; the Git base alone is not claimed as a clean release identity.
Snapshots are reviewed source dependencies, not arbitrary untrusted plugins.

In a product, use the recorded snapshot and its pinned identity:

```
python3 <snapshot>/compose.py compose --core <snapshot> \
  --output <fresh-build-directory> --product <product> --overlay <host-source>
```

Omit `--overlay` for neutral Core UI. One supplies its branding, session and account-return overlay. An overlay fills `src/host` and cannot
replace its generic registry. Symlinks, changed/unrecorded snapshot files and
environment files in host overlays are refused. Build tools execute trusted
source; this format is not a code sandbox or a replacement for signed/reviewed
release artifacts. New output directories are required to avoid overwriting a
checkout or previous build. Run `npm ci` and `npm run build` there. The frontend
uses its own type roots, eliminating inherited Electron and sibling checkout
type dependencies. Do not edit the vendored source dependency in place; regenerate
from Core and review the new identity.

`distribution-build.json` records product, source tree/commit, lockfile digest
and host-file digests. A product distribution manifest additionally records the
Core wheel/version/digest, embedded UI identity, desktop-host artifact/version,
trusted profile and App versions. Product releases are independent. A future
published UI package may replace the source snapshot with equivalent contracts.
One composes its reviewed UI into the pinned Core wheel, preserving engine files,
licenses and wheel integrity metadata. Its host overlay uses the shared editor
and introduces no independent frontend fork.
Business uses its composed host UI in the existing container recipe.

## Shared native host

The reusable `@integral/desktop-host` source, tests, LICENSE and NOTICE now live in
Integral One `packages/desktop-host`. Products pin its immutable packed artifact;
Business may reuse it without importing One account screens. The old Core desktop
product and packaging workflow are retired in the runtime-composition candidate.
Core retains its generic local launcher, storage/recovery and session contracts.
See [runtime composition](runtime-composition-v1.md). No registry publication or
signed installer release is implied by source ownership transfer.

## Compatibility and licensing

Qualify Core developer mode, One local mode, hosted Business and the separate
Business native client. Cross-product changes require explicit compatible pins;
no Core release automatically rolls out. Preserve historical Apache and dependency
notices and source provenance. Core and One remain Apache-2.0; Business remains private. No storage-engine replacement or silent
legacy-data migration accompanies product extraction.
