# Reference managed text channel

An independent, installable Python wheel that exercises the Integral managed
channel v1 contract. It uses synthetic in-memory state, no credentials,
network, model, provider operations or reply generation. It is **not** a
production adapter. Its single-process state intentionally disappears on exit
and provides no cross-worker persistence or authorization.

## Build and verify locally

From the repository root, use a reviewed Integral Core development environment
and the uv CLI. Install into a disposable target, not the Core environment
or a running deployment.

```sh
example=examples/reference-managed-text-channel
out="$(mktemp -d)"
uv build --wheel "$example" --out-dir "$out"
uv pip install --no-deps --target "$out/site" "$out"/integral_external_channel_fixture-1.0.0-py3-none-any.whl
PYTHONPATH="$PWD/backend:$out/site" backend/.venv/bin/python -P "$example/verify.py"
```

The Python -P flag prevents the source directory from shadowing the installed
wheel. Use --offline with uv if reviewed build dependencies are already
cached. For an installed Core wheel, use that Python interpreter and omit the
checkout's backend directory from PYTHONPATH.

Expected output: Add/registered, Configure/inactive, Configure/active,
Configure/inactive, Add/registered. The verifier admits the installed wheel
through the exact distribution/version/slug/factory allowlist. It does not
put an unreviewed factory on Core's import path, activate anything outside
its fixture, or start a listener. Use a fresh environment on repeated runs
rather than forcing replacement in a deployment.

Core's discovery and authenticated API tests build a separate test wheel
from the same adapter source. They test rejection before import, installed
discovery, scoped Add/Configure projection, exact provision/epoch guarding,
enable/disable, and removal. The standalone verifier uses synthetic
`ChannelScope` only to demonstrate the adapter contract. Production entry
points must accept only authenticated host scope, defend exact mutation at
their persistence boundary, and independently verify actual serving authority.

See the [Integral Connector Standard v1](../../docs/backend/connector-extension-standard-v1.md)
for artifact review, host bootstrap, and current limitations.
