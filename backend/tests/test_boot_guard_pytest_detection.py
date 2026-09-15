"""The TESTING boot guard must not fire during a pytest run.

Regression: ``app/main.py`` refuses to boot when ``TESTING`` is set, ``DEBUG``
is off, and the process is not pytest — a sound guard against shipping the auth
bypass middleware to production. It detected pytest via ``PYTEST_CURRENT_TEST``,
which pytest sets only while an individual test EXECUTES. ``app.main`` is
imported during COLLECTION (``from app.main import app``), so the variable was
absent and the guard called ``sys.exit(1)`` mid-collection.

Locally this never fired because ``backend/.env`` sets ``DEBUG=true``. CI has no
``.env``, so ``DEBUG`` defaults False and every run died with
``INTERNALERROR ... KeyError: <WorkerController gw1>`` as the xdist worker
exited under it.
"""

import os

from app.main import _effective_worker_count, _running_under_pytest


def test_detects_pytest_without_the_per_test_env_var(monkeypatch):
    """Detection must not depend on PYTEST_CURRENT_TEST.

    That variable is unset at import time, which is exactly when the guard runs.
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert _running_under_pytest() is True


def test_detects_pytest_via_the_per_test_env_var(monkeypatch):
    """The original signal still counts when present."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "some_test (call)")
    assert _running_under_pytest() is True


def test_guard_condition_is_false_under_pytest_even_with_testing_and_no_debug():
    """The full guard predicate must be inert here.

    Mirrors CI: TESTING set, DEBUG off. If this ever evaluates True, collection
    dies with SystemExit and the failure surfaces as an xdist INTERNALERROR
    rather than a readable test failure.
    """
    os.environ["TESTING"] = "1"
    testing_set = bool(os.getenv("TESTING") or os.getenv("test_mode"))
    would_exit = testing_set and not _running_under_pytest()
    assert would_exit is False


def test_effective_worker_count_respects_web_concurrency(monkeypatch):
    """Docker CMD uses WEB_CONCURRENCY; warning must see it even if WORKERS=1."""
    from app import config as config_mod

    monkeypatch.setattr(config_mod.settings, "WORKERS", 1)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert _effective_worker_count() == 2
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert _effective_worker_count() == 1
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr(config_mod.settings, "WORKERS", 3)
    assert _effective_worker_count() == 3
