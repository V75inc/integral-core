.PHONY: test test-serial test-cov test-full test-smoke lint verify

# ---------------------------------------------------------------------------
# PR CI has smoke, contract, PostgreSQL, frontend and artifact lanes. The broad
# default suite remains a local responsibility; both entrypoints share the
# root make targets so their environments and worker caps do not drift.
# ---------------------------------------------------------------------------

# Reuse the root gate's environment, worker cap and timing reports rather than
# maintaining a second default with different coverage and resource usage.
test:
	$(MAKE) -C .. test-backend

# Serial developer/debug run; does not require pytest-xdist. For a CI-env
# serial comparison use the root target with PYTEST_WORKERS=0.
test-serial:
	pytest

# Coverage run, retaining the existing local coverage threshold.
test-cov:
	TESTING=1 DEBUG=false INTEGRAL_ENV_FILE=/dev/null LITELLM_MODE=PRODUCTION \
	pytest -n auto --maxprocesses=4 --dist loadfile --durations=25 \
	--cov=app --cov-report=term-missing --cov-fail-under=30

# Full suite incl. the slow/integration tests pruned by default.
test-full:
	INTEGRAL_RUN_SLOW_TESTS=1 $(MAKE) -C .. test-backend PYTEST_MARKERS="not domain_app"

# The per-PR CI smoke selection, reproduced locally.
test-smoke:
	$(MAKE) -C .. verify-ci

# Lint / format / typecheck gate (matches the CI pre-commit step).
lint:
	$(MAKE) -C .. precommit

# Broad local gate, including frontend and artifact validation. PostgreSQL and
# opt-in domain-App suites remain separate targets because they need setup.
verify:
	$(MAKE) -C .. verify

# The complete default selection on Postgres. CI has a separate, narrower
# Postgres contract lane. The root target caps pools and workers for this run.
.PHONY: test-postgres test-domain-apps
test-postgres:
	$(MAKE) -C .. test-postgres

# The ~138 privilege/access tests excluded from every default run by the
# `not domain_app` filter in pyproject addopts.
test-domain-apps:
	INTEGRAL_INCLUDE_DOMAIN_APPS=1 pytest -n auto --maxprocesses=4 --dist loadfile -m domain_app
