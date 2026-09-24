# Releasing integral-core

integral-core ships via **Trusted Publishing** (OIDC). No API tokens live in
the repo.

| Tag shape | Index | Workflow |
| --- | --- | --- |
| Pre-release (`v0.1.1rc1`, `aN`, `bN`) | [TestPyPI](https://test.pypi.org/project/integral-core/) | [`publish-testpypi.yml`](.github/workflows/publish-testpypi.yml) |
| Final (`v0.1.1`) | [PyPI](https://pypi.org/project/integral-core/) | [`publish-pypi.yml`](.github/workflows/publish-pypi.yml) |

Both workflows fire on a push to `main` and on a `v*` tag. Each one reads
`backend/pyproject.toml`. A pre-release goes to TestPyPI. A final version
goes to PyPI. The other workflow skips. If that version is already on the
index, the run does not upload it again. After a successful publish, the
workflow pushes `v<version>` when that tag is missing. A merge of a version
bump is enough. A manual tag still publishes that commit.

## Versioning

- Single source of truth: `backend/pyproject.toml` → `[project].version`
- Follows [PEP 440](https://peps.python.org/pep-0440/) /
  [SemVer](https://semver.org/): `MAJOR.MINOR.PATCH`, with pre-releases as
  `rcN` / `aN` / `bN` (e.g. `0.1.1rc1`).
- A manual tag or a workflow-dispatch tag must match `pyproject.toml`.
  A push to `main` publishes the version already in that file.

## One-time setup

### TestPyPI (rc / pre-release)

1. https://test.pypi.org/manage/account/publishing/ → pending publisher:
   - Project: `integral-core`
   - Owner: `V75inc`
   - Repository: `integral-core`
   - Workflow: `publish-testpypi.yml`
   - Environment: `testpypi`
2. GitHub → [environment](https://github.com/V75inc/integral-core/settings/environments)
   named `testpypi`.

### PyPI (final)

1. https://pypi.org/manage/account/publishing/ → pending publisher:
   - Project: `integral-core`
   - Owner: `V75inc`
   - Repository: `integral-core`
   - Workflow: `publish-pypi.yml`
   - Environment: **(leave blank)** — the PyPI job does not use a GitHub
     environment; a non-blank value here causes `invalid-publisher`.

## Cutting a pre-release (TestPyPI)

1. Bump `[project].version` to an `rcN` (or `aN` / `bN`), e.g. `0.1.1rc2`.
2. Merge that commit to `main`.

3. `publish-testpypi.yml` runs `.ci/bundle_web_assets.sh` (production
   frontend build, `VITE_API_URL` empty, copied to `backend/app/web/static`),
   builds from `backend/`, refuses a wheel that lacks
   `app/web/static/index.html`, runs `twine check` and the isolated Core,
   SDK, and external-App artifact proofs, publishes to TestPyPI, and pushes
   `v<version>` if the tag is not already there. `publish-pypi.yml` no-ops
   and uses the same frontend bundle when it does publish.

4. Verify:

   ```bash
   pip download \
     --index-url https://test.pypi.org/simple \
     --no-deps \
     --dest ./wheels \
     'integral-core==0.1.1rc8' 'jvagent==0.1.8rc15'
   pip install \
     --index-url https://pypi.org/simple \
     ./wheels/integral_core-*.whl ./wheels/jvagent-*.whl
   integral init ./my-integral
   test -f ./my-integral/integral-apps/.gitkeep
   test ! -e ./my-integral/integral-apps/starter
   integral init ./with-app --slug studio-equipment
   integral web --help
   ```

   Do not add TestPyPI as a general extra index. Pip will then prefer a
   broken `fastapi` sdist published there. Download only these two
   pre-release wheels and resolve every other dependency from PyPI.
   `jvagent` is a normal version pin. A direct wheel URL is rejected at
   upload. `integral init` is on the wheel from `0.1.1rc4` (that cut writes
   a starter App). From `0.1.1rc5`, `integral init` is blank unless
   `--slug` is passed, and `integral web` serves the workspace.

## Cutting a final release (PyPI)

1. Bump `[project].version` to a final version, e.g. `0.1.1`.
2. Merge that commit to `main`.

3. `publish-pypi.yml` builds from `backend/`, runs `twine check` and the
   isolated Core, SDK, and external-App artifact proofs, publishes to PyPI,
   and pushes `v0.1.1` if the tag is not already there.
   `publish-testpypi.yml` no-ops.

4. Verify:

   ```bash
   pip install integral-core==0.1.1
   ```

## Commercial pin

Commercial `V75inc/integral` tracks Core by git tag until index installs are
stable — see `docs/product/CORE_PIN.md`.

TestPyPI (explicit index):

```toml
[tool.uv.sources]
integral-core = { index = "testpypi" }

[[tool.uv.index]]
name = "testpypi"
url = "https://test.pypi.org/simple/"
explicit = true
```

PyPI (default index once final is published):

```toml
# backend/pyproject.toml
dependencies = [
  "integral-core==0.1.1",
]
```

## Not in scope (yet)

- GHCR Core image publish (separate from the Python package)
- Release-gate wait on CI before publish
