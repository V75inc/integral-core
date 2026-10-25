# Integral — local verification gate.
#
# CI runs smoke, Core-only, contract, PostgreSQL, frontend and artifact lanes;
# the FULL default suite remains a local responsibility. That only works if "run it
# locally" is one command instead of tribal knowledge — this file is that
# command.
#
#   make verify        everything below, in order
#   make verify-pr     backend test lanes + postgres lane — pre-push
#   make verify-ci     smoke lane only (not the entire backend job)
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

# Bound collection/import memory and database IO on developer machines. Plain
# pytest stays serial; make targets use the same file grouping as PR CI.
# Override with PYTEST_WORKERS=0 for debugging, or raise both settings on a
# measured dedicated runner. PostgreSQL targets retain their separate limit.
PYTEST_WORKERS ?= auto
PYTEST_MAX_WORKERS ?= 4
PYTEST_DIST ?= loadfile
PYTEST_MARKERS ?= not domain_app and not slow
CONTRACT_TEST_WORKERS ?= 2
POSTGRES_TEST_WORKERS ?= 2
TEST_REPORT_DIR ?= $(CURDIR)/backend/.pytest_cache/verification
PYTEST_PARALLEL = -n $(PYTEST_WORKERS) --maxprocesses=$(PYTEST_MAX_WORKERS) --dist $(PYTEST_DIST)
PYTEST_ENV = TESTING=1 DEBUG=false INTEGRAL_ENV_FILE=/dev/null LITELLM_MODE=PRODUCTION
PYTEST_REPORT = --durations=25 --durations-min=0.1
# Independent per-worker PostgreSQL databases, with bounded prime/log pools.
# Keep real encoder qualification serial; these are graph/transaction lanes.
POSTGRES_TEST_ENV = $(PYTEST_ENV) INTEGRAL_TEST_DB=postgres JVSPATIAL_DB_TYPE=postgres \
                   JVSPATIAL_POSTGRES_DSN=$${JVSPATIAL_POSTGRES_DSN:-postgresql://integral:integral@localhost:5432/integral_core} \
                   JVSPATIAL_POSTGRES_MIN_POOL_SIZE=1 JVSPATIAL_POSTGRES_MAX_POOL_SIZE=3
POSTGRES_TEST_ARGS = -n $(POSTGRES_TEST_WORKERS) --dist worksteal $(PYTEST_REPORT)

# Every guard, including the four CI does not run. The pre-commit hook is the
# stronger gate by design; `verify` matches the hook, not CI.
GUARDS := jvspatial_drift_check graph_contiguousness_check \
          substrate_domain_drift_check service_layer_drift_check \
          ui_drift_check skill_compliance_check bundle_facade_check \
          tool_manifest_check csp_inline_script_hash_check \
          node_destroy_check nodes_len_drift_check \
          core_no_app_import_check core_packages_only_check contracts_boundary_check
GUARDS += module_boundary_check
GUARDS += module_import_cycle_check

.PHONY: help verify verify-pr verify-ci verify-core-only verify-contract build-asset-register verify-artifact verify-clean-install verify-sdk-artifact verify-external-asset-register verify-independent-artifacts test-backend test-frontend test-postgres test-postgres-ci types lint guards \
        precommit format-check audit clean-pyc capability-map

help:
	@echo "Integral verification targets"
	@echo ""
	@echo "  make verify         full gate: guards, format, types, artifact, frontend + CI-env backend"
	@echo "  make verify-pr      reproduce backend test lanes and Postgres — pre-push"
	@echo "  make verify-ci      reproduce the PR CI smoke lane only"
	@echo "  make verify-core-only  F0 Core-only lane (INTEGRAL_CORE_ONLY=1 + core_only marker)"
	@echo "  make verify-contract   F0 extension-contract lane (reference App)"
	@echo "  make build-asset-register  Build the external reference App archive into dist/"
	@echo "  make verify-artifact   Build and import public Core wheel outside source tree"
	@echo "  make verify-clean-install  Build, resolve, and import Core in a fresh venv"
	@echo "  make verify-sdk-artifact  Build and import the public SDK in a fresh venv"
	@echo "  make verify-external-asset-register  Load Asset Register against fresh wheels"
	@echo "  make verify-independent-artifacts  Run every isolated Core, SDK, and App wheel proof"
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
	@echo "  Backend workers: $(PYTEST_WORKERS), capped at $(PYTEST_MAX_WORKERS); PYTEST_WORKERS=0 for serial"
	@echo "  Contract / Postgres workers: $(CONTRACT_TEST_WORKERS) / $(POSTGRES_TEST_WORKERS) (0 for serial)"
	@echo "  Timing + JUnit reports: $(TEST_REPORT_DIR)"
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
# Ignore checkout dotenv files in both Core and LiteLLM, as on CI.
verify-ci:
	@echo "==> CI-faithful backend run (TESTING=1, DEBUG=false, smoke marker, xdist)"
	@mkdir -p "$(TEST_REPORT_DIR)"
	@cd backend && $(PYTEST_ENV) $(PY) -m pytest -q --tb=short \
		$(PYTEST_PARALLEL) $(PYTEST_REPORT) --junitxml="$(TEST_REPORT_DIR)/smoke.xml" \
		-m "smoke and not domain_app and not slow"

## F0 — Core-only library filter + marker lane
verify-core-only:
	@echo "==> F0 Core-only lane"
	@.ci/core_no_app_import_check.sh
	@.ci/core_packages_only_check.sh
	@cd backend && TESTING=1 INTEGRAL_CORE_ONLY=1 $(PY) -m pytest -q --tb=short \
		tests/core_only/ tests/contract/test_reference_hello_app.py \
		-m "core_only"

## F0 — external reference App contract tests
build-asset-register:
	@python3 examples/asset-register/build.py --out-dir dist

## W0.4 — regenerate docs/generated/capability-map.{json,md}; tests fail when stale
capability-map:
	@cd backend && $(PY) scripts/generate_capability_map.py

verify-artifact:
	@.ci/verify_artifact_baseline.sh

## C1 — Public wheel install proof. Resolves production dependencies, so this
## remains a release/developer lane rather than part of the offline-friendly
## local verify gate.
verify-clean-install:
	@.ci/verify_clean_install.sh

verify-sdk-artifact:
	@.ci/verify_sdk_artifact.sh

verify-external-asset-register:
	@.ci/verify_external_asset_register.sh

## C1/C4 — release-grade boundary proof. This intentionally resolves public
## dependencies and is kept out of the offline-friendly local `verify` target.
verify-independent-artifacts: verify-artifact verify-clean-install verify-sdk-artifact verify-external-asset-register
	@echo "==> Independent Core, SDK, and external App artifact proofs passed"

verify-contract:
	@echo "==> F0 extension-contract lane"
	@mkdir -p "$(TEST_REPORT_DIR)"
	@cd backend && $(PYTEST_ENV) $(PY) -m pytest -q --tb=short \
		-n $(CONTRACT_TEST_WORKERS) --dist worksteal $(PYTEST_REPORT) \
		--junitxml="$(TEST_REPORT_DIR)/contract.xml" tests/contract/ -m "contract"

test-backend:
	@echo "==> Full default backend suite (CI environment; includes smoke tests)"
	@mkdir -p "$(TEST_REPORT_DIR)"
	@cd backend && $(PYTEST_ENV) $(PY) -m pytest -q \
		$(PYTEST_PARALLEL) $(PYTEST_REPORT) --junitxml="$(TEST_REPORT_DIR)/backend.xml" \
		-m "$(PYTEST_MARKERS)"

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
	@mkdir -p "$(TEST_REPORT_DIR)"
	@cd backend && $(POSTGRES_TEST_ENV) $(PY) -m pytest -q --tb=short \
		$(POSTGRES_TEST_ARGS) --junitxml="$(TEST_REPORT_DIR)/postgres-spikes.xml" tests/spikes/ -m postgres
	@cd backend && $(POSTGRES_TEST_ENV) $(PY) -m pytest -q --tb=short \
		$(POSTGRES_TEST_ARGS) --junitxml="$(TEST_REPORT_DIR)/postgres-contract.xml" tests/contract/ -m "contract and postgres"
	@cd backend && $(POSTGRES_TEST_ENV) $(PY) -m pytest -q --tb=short \
		$(POSTGRES_TEST_ARGS) --junitxml="$(TEST_REPORT_DIR)/postgres-app-jobs.xml" \
		tests/test_app_jobs.py tests/test_tool_context_own_bundle_workspace.py
	@cd backend && $(POSTGRES_TEST_ENV) $(PY) -m pytest -q --tb=short \
		$(POSTGRES_TEST_ARGS) --junitxml="$(TEST_REPORT_DIR)/postgres-authority.xml" \
		tests/test_credentials_delivery.py tests/test_member_provision_authority.py \
		tests/test_entry_relation_admission_regressions.py tests/test_entry_template_resolve.py tests/test_install_seed_authority.py \
		tests/test_app_passage_candidates.py

## Reproduce backend test lanes, frontend tests and Postgres before pushing.
## Artifact CI checks have separate targets above. Catches black/isort
## drift, contract lane, smoke marker, and the separate postgres job — the
## three failure modes that turned PR #4 red (pre-commit reformat, wrong
## mock patch target, restore glob typo + env leak).
verify-pr: guards precommit format-check verify-core-only verify-contract verify-ci test-frontend test-postgres-ci
	@echo ""
	@echo "verify-pr: backend, frontend test and Postgres checks passed"

test-frontend:
	@echo "==> Frontend suite"
	@cd frontend && npm run test:run -- --reporter=default --reporter=junit \
		--outputFile=node_modules/.cache/verification/frontend.xml

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
	@uvx black@$(BLACK_VER) --check --extend-exclude='resident_harness' backend/app backend/tests
	@uvx isort@$(ISORT_VER) --profile black --check-only --skip-glob='**/resident_harness/**' backend/app backend/tests
	@uvx $(FLAKE8_PLUGINS) flake8@$(FLAKE8_VER) --config=backend/.flake8 backend/app

audit:
	@echo "==> Dependency advisories"
	@cd backend && bash ../.ci/dependency_audit.sh backend
	@cd frontend && bash ../.ci/dependency_audit.sh frontend

## Ordered cheapest-first so a fast failure surfaces before the slow suites.
## The complete default backend selection includes the smoke selection and now
## runs with the same no-dotenv/DEBUG=false environment and xdist file grouping.
## Do not execute that overlapping smoke selection a second time here. The
## independent verify-ci/verify-pr targets and PR CI retain their smoke lane.
verify: guards precommit format-check lint types verify-artifact test-frontend test-backend
	@echo ""
	@echo "verify: all checks passed"

clean-pyc:
	@find backend -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find backend -name '*.pyc' -delete 2>/dev/null || true
