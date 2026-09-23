# Releasing integral-core

integral-core ships via **Trusted Publishing** (OIDC). No API tokens live in
the repo.

| Tag shape | Index | Workflow |
| --- | --- | --- |
| Pre-release (`v0.1.1rc1`, `aN`, `bN`) | [TestPyPI](https://test.pypi.org/project/integral-core/) | [`publish-testpypi.yml`](.github/workflows/publish-testpypi.yml) |
| Final (`v0.1.1`) | [PyPI](https://pypi.org/project/integral-core/) | [`publish-pypi.yml`](.github/workflows/publish-pypi.yml) |

Both workflows fire on `v*` tags; each classifies the PEP 440 version and
skips when the tag is not for its index.

## Versioning

- Single source of truth: `backend/pyproject.toml` → `[project].version`
- Follows [PEP 440](https://peps.python.org/pep-0440/) /
  [SemVer](https://semver.org/): `MAJOR.MINOR.PATCH`, with pre-releases as
  `rcN` / `aN` / `bN` (e.g. `0.1.1rc1`).
- Each publish workflow **fails if the git tag does not match**
  `pyproject.toml`, so the two cannot drift.

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

1. Bump `[project].version` to an `rcN` (or `aN` / `bN`), e.g. `0.1.1rc1`.
2. Commit on `main`, tag, push:

   ```bash
   git tag v0.1.1rc1
   git push origin v0.1.1rc1
   ```

3. `publish-testpypi.yml` builds from `backend/`, runs `twine check` and the
   isolated Core, SDK, and external-App artifact proofs, then publishes to
   TestPyPI. `publish-pypi.yml` no-ops.

4. Verify:

   ```bash
   pip install -i https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ \
     integral-core==0.1.1rc1
   ```

## Cutting a final release (PyPI)

1. Bump `[project].version` to a final version, e.g. `0.1.1`.
2. Commit on `main`, tag, push:

   ```bash
   git tag v0.1.1
   git push origin v0.1.1
   ```

3. `publish-pypi.yml` builds from `backend/`, runs `twine check` and the
   isolated Core, SDK, and external-App artifact proofs, then publishes to
   PyPI. `publish-testpypi.yml` no-ops.

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

- Auto-tag on version bump
- GHCR Core image publish (separate from the Python package)
- Release-gate wait on CI before publish
