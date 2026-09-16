# Releasing integral-core

integral-core ships to [TestPyPI](https://test.pypi.org/project/integral-core/)
first via **Trusted Publishing** (OIDC). No API tokens are stored in the repo —
[`publish-testpypi.yml`](.github/workflows/publish-testpypi.yml) authenticates
using GitHub's OIDC identity.

Production PyPI is **not** wired yet. Cut TestPyPI releases until the first
final public index publish is approved.

## Versioning

- Single source of truth: `backend/pyproject.toml` → `[project].version`
- Follows [PEP 440](https://peps.python.org/pep-0440/) /
  [SemVer](https://semver.org/): `MAJOR.MINOR.PATCH`, with pre-releases as
  `rcN` / `aN` / `bN` (e.g. `0.1.1rc1`).
- The publish workflow **fails if the git tag does not match**
  `pyproject.toml`, so the two cannot drift.

## One-time setup (TestPyPI)

1. **TestPyPI** → https://test.pypi.org/manage/account/publishing/ → add a
   pending publisher:
   - PyPI Project Name: `integral-core`
   - Owner: `V75inc`
   - Repository name: `integral-core`
   - Workflow name: `publish-testpypi.yml`
   - Environment name: `testpypi`
2. In the GitHub repo, create a
   [environment](https://github.com/V75inc/integral-core/settings/environments)
   named `testpypi` (required for TestPyPI OIDC).

## Cutting a TestPyPI release

1. Bump `[project].version` in `backend/pyproject.toml` (use an `rcN` tag for
   dry runs if you want to keep `0.1.0` reserved).
2. Commit on `main`, then tag and push:

   ```bash
   git tag v0.1.1rc1
   git push origin v0.1.1rc1
   ```

3. The workflow builds sdist + wheel from `backend/`, runs `twine check`, then
   publishes to **TestPyPI**.

4. Verify the install:

   ```bash
   pip install -i https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ \
     integral-core==0.1.1rc1
   ```

   (`--extra-index-url` is required so dependencies resolve from real PyPI while
   the Core package itself comes from TestPyPI.)

## Commercial pin

Commercial `V75inc/integral` tracks Core by git tag until TestPyPI installs are
stable — see `docs/product/CORE_PIN.md`. After a TestPyPI cut lands, commercial
may switch:

```toml
[tool.uv.sources]
integral-core = { index = "testpypi" }

[[tool.uv.index]]
name = "testpypi"
url = "https://test.pypi.org/simple/"
explicit = true
```

## Not in scope (yet)

- Production PyPI Trusted Publisher / `publish-pypi` job
- Auto-tag on version bump
- GHCR Core image publish (separate from the Python package)
