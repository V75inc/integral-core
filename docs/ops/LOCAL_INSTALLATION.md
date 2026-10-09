# Your own Integral, with one command

Once the release containing this launcher and its dependencies is published to PyPI, the discovery command is:

```bash
uvx --python 3.12 --from integral-core integral up
```

With an installed package, run `integral up`. It prepares a private PostgreSQL database, starts Core and its packaged web interface, waits for both to be ready, and opens your browser. There is no separate Docker, database, Node, or frontend setup. The initial download requires internet access; later launches reuse the installed environments. AI provider access is configured separately in Settings → AI Models.

This checkout implements the launcher. Its version number alone does not establish a public package release. Use a reviewed Core wheel until the stable publication lane is ready. Runtime dependencies resolve from PyPI; no companion agent package is required.

## First launch

Integral AI is built in and selected automatically. No harness flag, agent identifier, companion package, or agent YAML is required. Create your account, open Settings → AI Models, and connect a provider/model. For a local Ollama model, choose Ollama and its local endpoint; no cloud API key is needed. A server can instead supply `INTEGRAL_NATIVE_MODEL=provider/model` and the corresponding provider credentials in `settings.env`. Without a model, Core still starts normally and directs chat users to model setup. It never switches harnesses.

## Try this checkout

Build the UI once, then use the source environment:

```bash
uv sync --directory backend --frozen --extra dev --extra test
./.ci/bundle_web_assets.sh
backend/.venv/bin/integral up
```

Use `Scripts/integral.exe` on Windows. The web interface prefers port 9006 and the API prefers 4000. The selected ports are retained across restarts when available. Occupied ports are handled automatically; `integral status` shows the actual addresses. Services bind to this computer's loopback interface. The API uses real authentication, PostgreSQL transactions and indexes, active rate limits, and `DEBUG=false`.

## Everyday commands

| Command | What happens |
| --- | --- |
| `integral up` | Start once, or reopen the existing installation. |
| `integral up --no-open --json` | Start without opening a browser; return a machine-readable descriptor. |
| `integral status` | Show whether the installation is ready, starting, or stopped. |
| `integral logs --follow` | Follow the API log; select `--service web`, `database`, or `supervisor` when needed. |
| `integral stop` | Stop owned processes and preserve data and keys. |
| `integral backup --output /safe/place/integral.tar.gz` | Stop and write a consistent private backup; leave Integral stopped. |
| `integral restore /safe/place/integral.tar.gz --home /empty/directory` | Restore into an empty installation directory. |
| `integral upgrade --version VERSION` | Install the chosen release separately, make a backup, and start it. |

Run `integral --help` for the command list and `integral COMMAND --help` for options. `--json` is available on every lifecycle command except `logs`. To request a web port, use `integral up --port 9006`; to inspect recent output without following it, use `integral logs --service api --lines 100`.

Every lifecycle command accepts `--home`. `INTEGRAL_HOME` also selects a data directory. Defaults are `~/Library/Application Support/Integral/Core` on macOS, `%LOCALAPPDATA%/Integral/Core` on Windows, and `$XDG_DATA_HOME/integral` (or `~/.local/share/integral`) on Linux. Data lives independently of uv's disposable tool environments. Repeated launches retain the same identity and encryption keys.

Use `integral up --apps /absolute/path/integral-apps` to discover external Apps. Core remembers this path for later launches. App discovery does not grant commercial entitlements or install Apps into a workspace. `integral init` remains the package-authoring command, and `integral web` remains available for connecting a packaged UI to an independently managed API.

Private operator settings may be placed in the installation's `settings.env`. The launcher keeps storage, signing keys, loopback addresses, real auth, and production-mode flags authoritative. It loads this file explicitly rather than importing a source checkout's `.env`. Keep this directory private.

## Authoring and client builds

The managed launcher and App authoring commands serve different stages of development:

| Command | Purpose |
| --- | --- |
| `integral init ./my-distro` | Create a blank distro with `.env`, a README, and an empty `integral-apps/` directory. |
| `integral init ./my-distro --slug my-app --name "My App"` | Also scaffold an App package. |
| `integral up --apps ./my-distro/integral-apps` | Run a managed installation that discovers those App definitions. |
| `integral web ./my-distro --api http://127.0.0.1:4000` | Serve the packaged UI against an API you already operate. |
| `integral export-web --output ./workspace-assets` | Export the packaged UI into a directory that does not yet exist, for a client build. |

`init --force` permits overwriting generated files. `web` accepts `--host` and `--port`; when `--api` is omitted, it reads the distro's `JVSPATIAL_PORT`, falling back to port 4000. Neither `web` nor `export-web` provisions a database or starts an API. Continue with the [App quickstart](../developer/quickstart.md) for package structure and extension contracts.

## Backups and upgrades

A backup contains PostgreSQL data, uploaded files, discovered App definitions, operator settings and encryption keys. It excludes disposable Python environments and lifecycle state. The archive contains private credentials and should be stored accordingly. Existing backup files are never overwritten. Symbolic links are refused rather than producing an incomplete portable backup.

These are cold physical PostgreSQL backups for the same operating system, architecture and bundled PostgreSQL version. They are not a cross-platform migration format. Restore into an empty directory with the compatible Core release, run `integral up --home /restored/directory`, and verify records, files and access before using it as your active installation. Restore retains installation identity and keys but does not retain an old machine's Python executable or external App path.

For a local release candidate:

```bash
integral upgrade --wheel /artifacts/integral_core-VERSION-py3-none-any.whl
```

The reviewed Core wheel contains its native harness binding and skills; normal dependencies resolve from PyPI. A successful upgrade selects its isolated interpreter for subsequent launches. A failed candidate leaves the pre-upgrade backup available; use the previous release to restore into a fresh directory. Core does not blindly roll back a database that may have undergone migrations.

## Runtime and client contract

The CLI's JSON descriptor has `contract_version: 1`, installation identity, state, home, API and web URLs, version, supervisor PID and process creation time, database kind, `default_harness_provider_id: integral_native`, and `harness_provider_id` for the running supervisor. It contains no tokens or encryption keys. `up` also returns `started`, determined while holding the installation lock. Clients wait for `state: ready`, consume `web_url`, and claim shutdown ownership only when `started` is true. Pass both `--if-pid` and `--if-created` to `stop` to avoid shutting down a replacement supervisor.

Core owns the database helper, API, workspace and their supervisor. Lifecycle operations are locked per installation. PID checks include process creation time. A private authenticated readiness handshake verifies the served installation rather than trusting any process listening on a port.

The PostgreSQL helper is pinned to pgserver 0.1.4 in a separate Python 3.12 environment. Available native wheels cover macOS Intel/Apple Silicon, Linux x86-64, and Windows x86-64. Linux ARM and other architectures are not qualified by this launcher. First-launch provisioning and native installers must be tested on each advertised platform before release.

Electron belongs in Integral Business. It calls this public launcher contract and loads the same Core workspace over loopback HTTP; Core contains no Electron or commercial App imports. See [deployment](DEPLOY.md) for hosted environments and [qualification](QUALIFICATION.md) for the separate release gates.

The packaged workspace serves HTML with `Cache-Control: no-store`, so upgrades do not reuse an old interface that names removed JavaScript assets. Business also clears its HTTP cache before opening a managed local workspace; account settings and saved model configuration remain in the installation.
