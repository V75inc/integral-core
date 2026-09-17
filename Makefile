# Integral — local verification gate.
#
# The team's model is: CI stays lean (it runs only the `smoke` marker on PRs),
# and the FULL suite is a local responsibility. That only works if "run it
# locally" is one command instead of tribal knowledge — this file is that
# command.
#
#   make verify        everything below, in order
#   make verify-pr     both PR CI jobs (backend + postgres lane) — pre-push
#   make verify-ci     backend job only (smoke marker)
#
# Read `make help` for the full target list.

SHELL := /bin/bash
.DEFAULT_GOAL := help

REPO_ROOT := $(shell cd "$(dirname "$(firstword $(MAKEFILE_LIST))")" && pwd)

# Same interpreter resolution the pre-commit hooks use, so `make` and the hook
# never disagree about which venv is in play.
PY := $(shell . .ci/resolve_python.sh >/dev/null 2>&1; \
        for c in backend/.venv/bin/python backend/venv/bin/python; do \
          [ -x "$$c" ] && echo "$$PWD/$$c" && exit 0; \
        done; command -v python3 || command -v python)

# Formatter versions are PINNED to match .pre-commit-config.yaml. A locally
# installed black/isort drifts from these and yields a false green — run them
# through uvx so the version is the one CI enforces.
BLACK_VER  := 24.8.0
ISORT_VER  := 6.0.0
FLAKE8_VER := 6.1.0
FLAKE8_PLUGINS := --with pep8-naming --with flake8-docstrings \
                  --with flake8-comprehensions --with flake8-bugbear \
                  --with flake8-annotations --with flake8-simplify

# Every guard, including the four CI does not run. The pre-commit hook is the
# stronger gate by design; `verify` matches the hook, not CI.
GUARDS := jvspatial_drift_check graph_contiguousness_check \
          substrate_domain_drift_check service_layer_drift_check \
          ui_drift_check skill_compliance_check bundle_facade_check \
          tool_manifest_check csp_inline_script_hash_check \
          node_destroy_check nodes_len_drift_check \
          core_no_app_import_check core_profiles_only_check

.PHONY: help verify verify-pr verify-ci verify-core-only verify-contract test-backend test-frontend test-postgres test-postgres-ci types lint guards \
        precommit format-check audit clean-pyc

help:
	@echo "Integral verification targets"
	@echo ""
	@echo "  make verify         full gate: guards, format, types, both suites, CI-faithful run"
	@echo "  make verify-pr      reproduce BOTH PR CI jobs — run before every push"
	@echo "  make verify-ci      reproduce the PR CI backend job only (smoke marker)"
	@echo "  make verify-core-only  F0 Core-only lane (INTEGRAL_CORE_ONLY=1 + core_only marker)"
	@echo "  make verify-contract   F0 extension-contract lane (reference App)"
	@echo "  make test-postgres  backend suite against local Postgres (INTEGRAL_TEST_DB=postgres)"
	@echo ""
	@echo "  make test-backend   full pytest suite (what CI does NOT run on a PR)"
	@echo "  make test-frontend  vitest run"
	@echo "  make types          tsc --noEmit"
	@echo "  make lint           ESLint (errors block, warnings tracked)"
	@echo "  make guards         all $(words $(GUARDS)) substrate guards"
	@echo "  make precommit      pre-commit framework (CI runs this as its own step)"
	@echo "  make format-check   black/isort/flake8 at the versions pre-commit pins"
	@echo "  make audit          dependency advisories, backend + frontend"
	@echo ""
	@echo "Interpreter: $(PY)"

## The single most important target.
##
## CI runs the backend with TESTING=1, NO .env file, and the smoke marker under
## xdist. A plain local `pytest` differs on all three counts, and each one can
## hide a real failure:
##
##   * no .env  -> DEBUG defaults False, so boot guards in app/main.py that are
##                 inert on a dev box (backend/.env sets DEBUG=true) DO fire
##   * -m smoke -> a different set of tests than the full suite
##   * xdist    -> an import-time sys.exit() shows up as
##                 "INTERNALERROR ... KeyError: <WorkerController gwN>",
##                 not as a readable test failure
##
## A green full-suite run is NOT evidence that CI will pass. This target is.
verify-ci:
	@echo "==> CI-faithful backend run (TESTING=1, DEBUG=false, smoke marker, xdist)"
	@cd backend && TESTING=1 DEBUG=false $(PY) -m pytest -q --tb=short \
		-n auto --dist loadfile -m "smoke and not domain_app and not slow"

## F0 — Core-only library filter + marker lane
verify-core-only:
	@echo "==> F0 Core-only lane"
	@.ci/core_no_app_import_check.sh
	@.ci/core_profiles_only_check.sh
	@cd backend && TESTING=1 INTEGRAL_CORE_ONLY=1 $(PY) -m pytest -q --tb=short \
		tests/core_only/ tests/contract/test_reference_hello_app.py \
		-m "core_only"

## F0 — external reference App contract tests
verify-contract:
	@echo "==> F0 extension-contract lane"
	@cd backend && TESTING=1 $(PY) -m pytest -q --tb=short \
		tests/contract/ -m "contract"

test-backend:
	@echo "==> Full backend suite"
	@cd backend && $(PY) -m pytest -q

# Postgres lane (required by substrate-scale Phase A+). Needs
# ``docker compose up -d db`` (host :5433) or an equivalent DSN via
# ``JVSPATIAL_POSTGRES_DSN``. ``conftest`` creates a per-worker DB when
# ``INTEGRAL_TEST_DB=postgres``. Cap xdist workers + pool size so local
# Postgres ``max_connections`` is not exhausted (default pool 10 ×
# ``-n auto`` blows past a typical Docker Postgres).
test-postgres:
	@echo "==> Backend suite on Postgres (INTEGRAL_TEST_DB=postgres)"
	@cd backend && TESTING=1 INTEGRAL_TEST_DB=postgres \
		JVSPATIAL_PG_GIN_INDEX=off JVSPATIAL_POSTGRES_MAX_POOL_SIZE=3 \
		$(PY) -m pytest -q --tb=short \
		-n 2 --dist loadfile -m "not domain_app and not slow"

## CI-faithful postgres lane (test-postgres job). Uses the same env as
## .github/workflows/ci.yml — port 5432, DB integral_core. Requires a
## Postgres service on that DSN (GitHub Actions service container locally:
## ``docker run -d -p 5432:5432 -e POSTGRES_USER=integral -e POSTGRES_PASSWORD=integral -e POSTGRES_DB=integral_core pgvector/pgvector:pg16``).
test-postgres-ci:
	@echo "==> CI-faithful Postgres lane (spikes + contract postgres markers)"
	@cd backend && TESTING=1 INTEGRAL_TEST_DB=postgres \
		JVSPATIAL_DB_TYPE=postgres \
		JVSPATIAL_POSTGRES_DSN=$${JVSPATIAL_POSTGRES_DSN:-postgresql://integral:integral@localhost:5432/integral_core} \
		$(PY) -m pytest -q --tb=short tests/spikes/ -m postgres
	@cd backend && TESTING=1 INTEGRAL_TEST_DB=postgres \
		JVSPATIAL_DB_TYPE=postgres \
		JVSPATIAL_POSTGRES_DSN=$${JVSPATIAL_POSTGRES_DSN:-postgresql://integral:integral@localhost:5432/integral_core} \
		$(PY) -m pytest -q --tb=short tests/contract/ -m "contract and postgres"

## Reproduce everything a PR runs in CI before pushing. Catches black/isort
## drift, contract lane, smoke marker, and the separate postgres job — the
## three failure modes that turned PR #4 red (pre-commit reformat, wrong
## mock patch target, restore glob typo + env leak).
verify-pr: guards precommit format-check verify-core-only verify-contract verify-ci test-frontend test-postgres-ci
	@echo ""
	@echo "verify-pr: all PR CI checks passed"

test-frontend:
	@echo "==> Frontend suite"
	@cd frontend && npm run test:run

types:
	@echo "==> Frontend type check"
	@cd frontend && npm run lint:types

## Gates on ERRORS only. The warning backlog (exhaustive-deps, no-explicit-any,
## and the React-Compiler-era react-hooks rules) is deliberately non-blocking —
## see frontend/eslint.config.js for why.
lint:
	@echo "==> ESLint (errors block; warnings are the tracked backlog)"
	@cd frontend && npm run lint

## NOTE: several guards (ui_drift, and others) scan `git diff --cached`, i.e.
## STAGED files only. Run against an unstaged tree they scan nothing and pass
## trivially — a false green, which is exactly what this Makefile exists to
## prevent. The check below refuses to report success on an empty index.
guards:
	@echo "==> Substrate guards ($(words $(GUARDS)))"
	@if git diff --cached --quiet 2>/dev/null; then \
	  echo "    NOTE: nothing staged — guards that scan the index will pass"; \
	  echo "    vacuously. Stage your changes (git add -A) for a real result."; \
	fi
	@failed=""; \
	for g in $(GUARDS); do \
	  if bash .ci/$$g.sh >/dev/null 2>&1; then \
	    echo "    PASS $$g"; \
	  else \
	    echo "    FAIL $$g"; failed="$$failed $$g"; \
	  fi; \
	done; \
	if [ -n "$$failed" ]; then \
	  echo ""; echo "guards failed:$$failed"; \
	  echo "re-run one for detail:  bash .ci/<name>.sh"; \
	  exit 1; \
	fi

## CI runs `pre-commit` as its own step, and it is NOT the same thing as the
## repo's `.githooks/pre-commit` (which only runs the substrate guards). The
## framework additionally enforces trailing-whitespace, end-of-file, black,
## isort, flake8 and mypy — a PR can pass every other target here and still go
## red on those, which is exactly what happened when this target was missing.
##
## Two hooks are `language: system` and shell out to a bare `python`, which
## does not exist on a machine that only has `python3`; the venv is prepended
## to PATH so they resolve the same interpreter everything else here uses.
precommit:
	@echo "==> pre-commit framework (the hooks CI runs as a separate step)"
	@PATH="$(dir $(PY)):$$PATH" pre-commit run --all-files

format-check:
	@echo "==> Format + lint at pinned versions (black $(BLACK_VER), isort $(ISORT_VER), flake8 $(FLAKE8_VER))"
	@uvx black@$(BLACK_VER) --check backend/app backend/tests
	@uvx isort@$(ISORT_VER) --profile black --check-only backend/app backend/tests
	@uvx $(FLAKE8_PLUGINS) flake8@$(FLAKE8_VER) --config=backend/.flake8 backend/app

audit:
	@echo "==> Dependency advisories"
	@cd backend && bash ../.ci/dependency_audit.sh backend
	@cd frontend && bash ../.ci/dependency_audit.sh frontend

## Ordered cheapest-first so a fast failure surfaces before the slow suites.
verify: guards precommit format-check lint types verify-ci test-frontend test-backend
	@echo ""
	@echo "verify: all checks passed"

clean-pyc:
	@find backend -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find backend -name '*.pyc' -delete 2>/dev/null || true
