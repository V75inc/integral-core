# Bundled Cua Driver

`electron-builder` copies the contents of this directory to
`<resourcesPath>/driver/` at package time — deliberately **outside** the asar,
because a native executable cannot be exec'd from inside an archive.

Resolution order in `src/driver-resource.js` is: `INTEGRAL_DRIVER_COMMAND`,
then this bundled path, then `PATH`. Bundled wins over `PATH` so a shipped
binary is never shadowed by whatever happens to be installed on the machine.

## Provisioning

This directory ships empty on purpose. Nothing here downloads anything —
a build that fetched a binary over the network would make every pack
non-reproducible and would put an unpinned vendor artifact in the supply chain.

**Most releases will not need this.** On Windows and Linux the app
auto-installs a checksum-verified driver into its own `userData/driver`
(**Environment → Install Cua Driver Automatically…**), and on macOS the vendor's
installer places `CuaDriver.app` in `/Applications`. Bundling is for the case
where Integral must not require any separate install at all.

To bundle, place the platform binary here before `npm run dist`:

| Platform | File               | Executable bit |
|----------|--------------------|----------------|
| macOS    | `cua-driver`       | required       |
| Windows  | `cua-driver.exe`   | n/a            |
| Linux    | `cua-driver`       | required       |

The **nested binary must be signed before the enclosing app is signed and
notarized** — an unsigned nested executable invalidates the outer signature.

## You probably do not need this yet

On macOS, a driver found on `PATH` runs `cua-driver mcp`, which proxies to the
installed `CuaDriver.app` daemon so the Accessibility and Screen Recording
grants stay attached to the app bundle identity. That route needs no bundling,
no nested signing, and no notarization, and it is the documented default.

Bundling only becomes necessary when Integral must not require a separate
install. `desktop/README.md` documents all three routes.

## If you bundle it anyway

Verify it before shipping. The vendor publishes a `checksums.txt` per release in
standard sha256sum format:

```bash
curl -fsSL "https://github.com/trycua/cua/releases/download/<TAG>/checksums.txt"
shasum -a 256 cua-driver
```

Their own installer does **not** do this — it downloads the binary and execs it
with no integrity check at all. `src/driver-install.js` does check, which is why
auto-install can be offered safely on Windows and Linux.
