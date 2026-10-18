# Contributing to Integral

Thank you for contributing. This repo is a monorepo (React frontend, Python/jvspatial backend, and the built-in Integral AI resident).

## Getting started

1. Clone the repo and copy `.env.example` to `.env`.
2. Enable git hooks (one-time per clone):

   ```bash
   git config core.hooksPath .githooks
   ```

3. Follow [README.md](README.md) for backend and frontend setup.

**Runtime dependencies:** `backend/pyproject.toml` and `backend/uv.lock`
define the single supported runtime. Install with
`uv sync --frozen --extra dev --extra test`. Integral AI is built in and always
active; no harness selection or activation environment variable is required.
Configure a model through Settings → AI Models.

## Development workflow

1. Create a feature branch from the current integration branch.
2. Make changes following [AGENTS.md](AGENTS.md) (jvspatial conventions, `@endpoint`, graph contiguousness).
3. Run tests and linters before opening a PR.
4. Pre-commit hooks must pass. Use `--no-verify` only for documented exceptions (see [.githooks/README.md](.githooks/README.md)).

## Branch model & protection policy

- **`dev`** — active development; all work lands here first.
- **`prod`** — current production trunk, fed by `dev` → `prod` pull requests.
- **`main`** — being promoted to the canonical production trunk (transition in
  progress); until then, treat it as the release target being stood up.

**Only Eldon Marks (`@eldonm`) and Asa Brouet (`@abrouet`) may merge or push
to `main` / `prod`.** Everyone else branches off `dev` and opens a PR. Never
force-push to `main` or `prod`.

> **Enforcement note (GitHub Free, private repo).** Branch-protection rules and
> rulesets are *created but NOT enforced* on private repos under the Free org
> plan — enforcement requires GitHub **Team**/Enterprise. On Free, repository
> write is all-or-nothing (any writer can push to any branch, including
> `main`/`prod`), so the rule above is **convention-only** until the `V75inc`
> org upgrades. Action items:
>
> - [ ] Upgrade the `V75inc` org to **GitHub Team** to enforce protection on `main`/`prod`.
> - [ ] Then: require a PR + green CI (`test-backend`, `test-frontend`), restrict
>   push on `main`/`prod` to `@eldonm` + `@abrouet`, block force-pushes and deletions.
> - [ ] **Authors cannot approve their own PRs on GitHub** (platform rule). Solo
>   merges use [`.github/workflows/auto-approve.yml`](.github/workflows/auto-approve.yml)
>   (bot approval after CI) or set `required_approving_review_count: 0` via
>   [`.github/scripts/apply-branch-protection.sh`](.github/scripts/apply-branch-protection.sh).
> - [ ] Audit collaborators; remove stale `write`/`admin` access (only the two
>   names above should retain push to the prod trunks).

## Substrate-touching changes

If your change affects the graph model, permissions, or Operational Model runtime, read:

- [docs/INVARIANTS.md](docs/INVARIANTS.md) — I-GRAPH-01, I-GRAPH-02, edge naming
- [docs/product/ARCHITECTURE.md](docs/product/ARCHITECTURE.md) §9 — access model

Plan-checker / review should enumerate which invariants your change preserves.

## Testing

### Backend

```bash
cd backend
pytest
pytest --cov=app
mypy app/
black app/ && isort app/
flake8 app/
```

### Frontend

```bash
cd frontend
npm run test:run
npm run lint:types
```

## Documentation

- **Product / strategy:** [docs/product/](docs/product/) — CONCEPT, PRD, ARCHITECTURE, ROADMAP, BYOA
- **Technical reference:** [docs/README.md](docs/README.md)
- **Breaking API/graph changes:** [CHANGELOG.md](CHANGELOG.md)

Update docs in the same PR when you change behavior users or integrators rely on.

## Pull requests

- Keep PRs focused; link to phase or issue when applicable.
- Include a short test plan in the PR description.
- Do not commit `.env` or secrets.
- **Self-approval:** when branch protection requires one review, authors may
  approve their own PR after CI passes (see branch-protection config above).
  Use stacked PRs (`base` = prior feature branch) for sequential merges.

## License

The repository currently declares Apache License 2.0 for Integral Core. Do not treat this statement as a retroactive assignment or as changing terms that applied when earlier contributions were submitted. Future contribution terms must be reviewed and published before they take effect; until then, contributors should submit only work they are authorized to contribute and clearly identify any third-party material and its license.

## Developer and App ecosystem

Core remains public Apache-2.0. See [developers and Apps](docs/community/DEVELOPERS-AND-APPS.md)
for repository ownership, reference App onboarding, contribution expectations and
the planned marketplace path. No account is required to clone/build or contribute
to Core or independently author Apps. One's official signup/service flow does not
change those Apache source rights.
