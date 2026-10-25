# Releasing integral-core

integral-core ships via **Trusted Publishing** (OIDC). No API tokens live in
the repo.

| Tag shape | Index | Workflow |
| --- | --- | --- |
| Pre-release (`v0.1.1rc1`, `aN`, `bN`) | [TestPyPI](https://test.pypi.org/project/integral-core/) | [`publish-testpypi.yml`](.github/workflows/publish-testpypi.yml) |
| Final (`v0.1.1`) | [PyPI](https://pypi.org/project/integral-core/) | [`publish-pypi.yml`](.github/workflows/publish-pypi.yml) |

Both workflows start after successful CI on the exact `main` revision, or
through manual workflow dispatch for a tag whose exact revision has passed CI.
Each reads `backend/pyproject.toml`. A pre-release goes to TestPyPI; a final
version goes to PyPI. The other workflow skips. If that version is already on
the index, the run does not upload it again. After successful publication, the
workflow records `v<version>` when that tag is missing. Merging a version bump
is enough to start this sequence. Pushing a tag alone does not publish it.

## Versioning

- Single source of truth: `backend/pyproject.toml` → `[project].version`
- Keep the editable project's version in `backend/uv.lock` and the source-only
  fallback in `backend/app/cli.py` aligned with that declaration. For a
  version-only change, run `uv lock --offline` from `backend/` and verify that
  no dependency versions changed. Update `CHANGELOG.md` in the same PR.
- Follows [PEP 440](https://peps.python.org/pep-0440/) /
  [SemVer](https://semver.org/): `MAJOR.MINOR.PATCH`, with pre-releases as
  `rcN` / `aN` / `bN` (e.g. `0.1.1rc1`).
- A manual tag or a workflow-dispatch tag must match `pyproject.toml`.
  A push to `main` publishes the version already in that file.

When a candidate spans stacked PRs, put the single version bump on the final PR
and merge its base PR first. Qualify each exact head; a green base does not
qualify its follow-on. Verify the actual CI workflow run when a stacked target
is outside the pull-request branch filters. The final `main` revision must pass
its own CI before publication. Preparation, a version declaration and a passing
PR run do not establish that the candidate has been uploaded or deployed.

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

1. Before changing the version, qualify the exact clean `main` revision:
   `make verify`, `make verify-pr`, `make verify-independent-artifacts`, and
   `make audit` must pass. Then install the built distribution into an isolated
   environment and exercise `integral init`, `integral web`, and the browser
   against a fresh PostgreSQL database. Record the tested source SHA, artifact
   digest, database topology, and any skipped or blocked checks in the
   acceptance evidence. A source test or successful build alone is not a
   deployment smoke test.
2. Bump `[project].version` to the next unused `rcN` (or `aN` / `bN`) and
   update the changelog. Check the target index first; published versions
   cannot be overwritten.
3. Merge that commit to `main`. Publication starts only after the CI workflow
   completes successfully for that exact commit SHA. Manual dispatch checks
   the same SHA against completed CI before building.

4. `publish-testpypi.yml` first verifies source CI for the exact commit, then
   runs `.ci/bundle_web_assets.sh` (production frontend build,
   `VITE_API_URL` empty, copied to `backend/app/web/static`) and
   `.ci/bundle_resident_harness.sh`, builds from `backend/`, refuses a wheel
   that lacks
   `app/web/static/index.html`, runs `twine check` and the isolated Core,
   SDK, and external-App artifact proofs, publishes to TestPyPI, and records
   `v<version>` if the tag is not already there. `publish-pypi.yml` no-ops
   and uses the same web and resident-harness bundle steps when it does
   publish.

5. Verify:

   ```bash
   pip download \
     --index-url https://test.pypi.org/simple \
     --no-deps \
     --dest ./wheels \
     'integral-core==<published-candidate>'
   pip install \
     --index-url https://pypi.org/simple \
     ./wheels/integral_core-*.whl
   integral init ./my-integral
   test -f ./my-integral/integral-apps/.gitkeep
   test ! -e ./my-integral/integral-apps/starter
   integral init ./with-app --slug studio-equipment
   integral web --help
   ```

   Do not add TestPyPI as a general extra index. Pip will then prefer a
   unrelated packages published there. Download only the Core candidate
   wheel and resolve its runtime dependencies from PyPI. Substitute an actually
   published candidate above; a locally built version is not publication proof. `integral init` is on the wheel from `0.1.1rc4` (that cut writes
   a starter App). From `0.1.1rc5`, `integral init` is blank unless
   `--slug` is passed, and `integral web` serves the workspace.

## Cutting a final release (PyPI)

1. Bump `[project].version` to a final version, e.g. `0.1.1`.
2. Merge that commit to `main`.

3. `publish-pypi.yml` first verifies source CI for the exact commit, then
   builds from `backend/`, runs `twine check` and the
   isolated Core, SDK, and external-App artifact proofs, publishes to PyPI,
   and records `v0.1.1` if the tag is not already there.
   `publish-testpypi.yml` no-ops.

4. Verify:

   ```bash
   pip install integral-core==0.1.1
   ```

## Downstream version pins

Downstream deployments should pin an exact published Core version and record
the selected artifact's identity. Update a consuming repository only after
verifying publication and the installed version; a source version bump alone
does not make an installable release available.

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
