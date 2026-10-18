# Integral Desktop

A standalone native window for Integral Core. Opening Integral starts a private
PostgreSQL database, API and web interface using Core's public `integral up`
launcher. No Docker, terminal commands, separately installed Python or database
are needed by an installer user. The first launch needs an internet connection
for Python and package downloads; this is not an offline installer.

## First use

1. Open the installer for your operating system and launch **Integral**.
2. Wait for **Preparing your private workspace** to finish. Create your local
   account in the window. The account and its workspaces belong to this local
   installation, independently of any hosted Integral account. If email delivery
   is not configured, choose **Skip for now** on email verification; it does not
   block local use.
3. Choose **File → Set Up AI Model…**, or **Settings → AI Models**. Choose your
   provider, enter your own API key and model, test the connection, and save.
   Ollama Cloud needs its API key and cloud model; a local Ollama server needs
   its reachable local server URL and installed model instead.
4. Open Agent and send a short message to verify the selected model.

Provider credentials are handled by Core's existing encrypted credential store.
The desktop has no key entry bridge, billing, subscriptions or App catalog of its
own. Provider usage is billed by your provider. No commercial Apps are included.
Optional microphone access is separate from model setup and is requested only
with consent. Dictation may also need a supported speech model in Settings.

Data persists in the operating system's Integral application-data directory under
`core/`. **File → Open Installation Folder** locates it. Preserve this folder,
including its encryption keys, when moving an installation. Quitting stops only
the runtime started by this window; attaching to an existing runtime leaves it
running. Removal of the program must preserve data unless the user deliberately
chooses to delete the installation.

## Development and packaging

Install Node.js, then run `npm ci` and `npm test` in this directory. Development
uses `integral` on PATH. To select a reviewed Core virtual environment, set
`INTEGRAL_CORE_COMMAND=/absolute/path/to/python` and
`INTEGRAL_CORE_ARGS='["-m","app.cli"]'`. Run `npm start`.

`INTEGRAL_HOME` or `--home=/absolute/path` selects an independent installation.
To connect to a hosted workspace instead, set `INTEGRAL_WORKSPACE_URL` or use
`--workspace-url=https://your-host`. Hosted mode starts no local services. Only
HTTPS is allowed except HTTP on loopback.

Build a reviewed Core wheel with its packaged web assets as described in
[RELEASING.md](../RELEASING.md). Set `INTEGRAL_CORE_WHEEL` to that wheel and
`INTEGRAL_UV_BINARY` to a native `uv` executable for the target platform. `npm run
pack` produces an unpacked application; `npm run dist` produces installers.
The bundler validates Core's public launcher contract and records checksums;
the launcher verifies the wheel and uv before execution. Never distribute a
source checkout, `.env`, user installation or provider keys inside an installer.

Targets are macOS DMG/ZIP, Windows NSIS/portable and Linux AppImage/deb. Linux ARM
is not supported by Core's managed PostgreSQL dependency. Each platform needs
its own clean-machine qualification. Signing/notarization and uninstall data
retention are release gates; an unsigned development build is not a published
installer. CI checks the desktop contracts on all three operating systems.

The renderer uses Core's current web UI, with sandboxing, context isolation and
no Node integration or privileged preload. Navigation stays on the workspace
origin; ordinary external links open in the system browser. See Electron's
[security guidance](https://www.electronjs.org/docs/latest/tutorial/security).
Native computer automation and folder access are not included in this port.

Source provenance and Apache-2.0 terms are retained in [NOTICE](NOTICE) and
[LICENSE](LICENSE). Business can remain a separate distribution using the same
public Core launcher; no commercial code is needed for standalone Core.
