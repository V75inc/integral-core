.PHONY: test test-serial test-cov test-full test-smoke lint verify

# ---------------------------------------------------------------------------
# CI runs only `test-smoke` + guards on a pull request (see
# .github/workflows/ci.yml). Everything below `verify` is what CI *stopped*
# doing to keep the free-tier minutes for merges and nightly — so `make verify`
# before you push is the real gate now, not the PR check.
# ---------------------------------------------------------------------------

# Fast local run — parallel across all cores via pytest-xdist. The conftest is
# xdist-safe (per-worker DB paths / graph-shell templates / postgres DBs keyed
# on PYTEST_XDIST_WORKER). Requires pytest-xdist (`pip install -r
# --extra test`). --dist loadfile keeps a whole test file on one worker
# so per-test DB + graph-context fixtures stay coherent.
test:
	pytest -n auto --dist loadfile

# Serial run — for debugging (readable output, works without pytest-xdist).
test-serial:
	pytest

# Coverage run — mirrors the push-to-main CI gate.
test-cov:
	pytest -n auto --dist loadfile --cov=app --cov-report=term-missing --cov-fail-under=30

# Full suite incl. the slow/integration tests pruned by default.
test-full:
	INTEGRAL_RUN_SLOW_TESTS=1 pytest -n auto --dist loadfile

# The per-PR CI gate, reproduced locally. Seconds, not minutes — run it freely.
test-smoke:
	pytest -n auto --dist loadfile -m "smoke and not domain_app and not slow"

# Lint / format / typecheck gate (matches the CI pre-commit step).
lint:
	pre-commit run --all-files

# Pre-push gate. Run this before opening or updating a PR: it covers what CI
# deliberately no longer runs per-PR. Fails on the first failing stage.
#   1. every substrate guard (CI runs 4 of 8; the git hook runs all 8)
#   2. lint / format / typecheck across the tree
#   3. the full backend suite in parallel
# Postgres mode and the domain-app privilege suites are separate targets below
# because they need extra setup; run them when touching those areas.
verify:
	@echo "==> substrate guards (all 8)"
	@cd .. && bash .githooks/pre-commit
	@echo "==> lint / typecheck"
	$(MAKE) lint
	@echo "==> full backend suite"
	$(MAKE) test
	@echo "==> OK: safe to push"

# The prod database backend. CI never exercises Postgres (it runs JsonDB), so
# this is the only place sort/pagination behaviour gets checked against what
# production actually runs. Needs `docker compose up -d db`.
.PHONY: test-postgres test-domain-apps
test-postgres:
	INTEGRAL_TEST_DB=postgres \
	INTEGRAL_TEST_POSTGRES_DSN=$${INTEGRAL_TEST_POSTGRES_DSN:-postgresql://integral:integral@localhost:5433/integral} \
	pytest -n auto --dist loadfile

# The ~138 privilege/access tests excluded from every default run by the
# `not domain_app` filter in pyproject addopts.
test-domain-apps:
	INTEGRAL_INCLUDE_DOMAIN_APPS=1 pytest -n auto --dist loadfile -m domain_app
