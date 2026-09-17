"""Test configuration and fixtures for Integral API tests."""

import contextlib
import inspect
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest
from httpx import ASGITransport, AsyncClient

# Pin the working directory to backend/ for the whole session. Many tests and
# bundle/profile helpers resolve paths relative to the backend root (e.g.
# ``Path("app/profiles")``, ``app/agentive/...`` manifests), so the suite only
# resolves correctly when cwd is backend/. Doing this at conftest import time
# (before the ``app`` import below and before any collection-time path lookup)
# makes the suite cwd-independent: it passes whether pytest is invoked from
# backend/ or from the repo root (e.g. ``pytest backend/tests/...``).
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Per-worker DB paths for pytest-xdist isolation.
_XDIST_WORKER = os.getenv("PYTEST_XDIST_WORKER", "master")
_DB_SUFFIX = "" if _XDIST_WORKER == "master" else f"_{_XDIST_WORKER}"

# Set test database BEFORE importing app
TEST_DB_PATH = f"test_integral_db{_DB_SUFFIX}"
TEST_LOG_DB_PATH = f"test_integral_logs{_DB_SUFFIX}"

# Opt-in Postgres test mode. JSON remains the default because the
# per-test-reset fixtures further down (``setup_test_db`` /
# ``_swap_prime_and_log_dbs``) are coupled to file-backed JsonDB
# semantics — they rely on per-test ``tmp_path`` directories. Switching
# to Postgres for the full suite is a separate piece of fixture surgery.
# When INTEGRAL_TEST_DB=postgres is set, the env vars below point at a
# per-worker test database on the docker-compose ``db`` service (see
# docker-compose.yml). The DB is dropped + re-created at session start
# so each run begins clean; per-test isolation in this mode currently
# relies on tests that override JVSPATIAL_DB_PATH skipping cleanly (the
# PG path ignores the path knob), so the suite tightness for PG mode
# is a known follow-up.
_TEST_DB_KIND = (os.getenv("INTEGRAL_TEST_DB") or "json").lower()

if _TEST_DB_KIND in ("postgres", "postgresql"):
    _PG_TEST_DB_NAME = f"integral_test{_DB_SUFFIX}"
    _PG_TEST_DSN = os.getenv(
        "INTEGRAL_TEST_POSTGRES_DSN",
        os.getenv(
            "JVSPATIAL_POSTGRES_DSN",
            f"postgresql://integral:integral@localhost:5433/{_PG_TEST_DB_NAME}",
        ),
    )
    os.environ["JVSPATIAL_DB_TYPE"] = "postgres"
    os.environ["JVSPATIAL_POSTGRES_DSN"] = _PG_TEST_DSN
    # Log DB mirrors prime (same postgres). Isolate via JVSPATIAL_LOG_DB_TYPE if needed.
    os.environ.pop("JVSPATIAL_DB_PATH", None)
    os.environ.pop("JVSPATIAL_LOG_DB_TYPE", None)
    os.environ.pop("JVSPATIAL_LOG_DB_PATH", None)
else:
    os.environ["JVSPATIAL_DB_PATH"] = TEST_DB_PATH
    os.environ["JVSPATIAL_DB_TYPE"] = "json"
    os.environ["JVSPATIAL_LOG_DB_TYPE"] = "json"
    os.environ["JVSPATIAL_LOG_DB_PATH"] = TEST_LOG_DB_PATH
os.environ["JVSPATIAL_DB_LOGGING_ENABLED"] = "true"
os.environ["JVSPATIAL_DB_LOGGING_DB_NAME"] = "logs"
# Wrap the prime DB in ObservableDatabase so db_op_counter counts round-trips.
# The perf budget tests (test_perf_query_budget.py) read X-DB-Round-Trip-Count,
# which is 0 unless this wrapper is active. Per-op overhead is a counter bump
# + one time.monotonic() call — negligible for the suite.
os.environ["JVSPATIAL_OBSERVABILITY_ENABLED"] = "1"
os.environ["TESTING"] = "1"
# bcrypt dominates fast-auth bootstrap (~700ms/register+login at rounds=12).
# Tests still exercise hashing; production default stays 12 via jvspatial.
os.environ["JVSPATIAL_BCRYPT_ROUNDS"] = "4"
os.environ["EMBEDDING_MODEL_EAGER_LOAD"] = "0"
# Disable the auth rate limiter for the suite (dev .env sets this too). The
# full suite makes many /auth/signup + /auth/reset-password calls from one test
# IP, which otherwise trips the 5/60s window → spurious 429s in CI (where .env
# isn't sourced). No test exercises the limiter.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
# Wave 3 of the SaaS-deployment plan removed the ``sqlite_vec`` driver
# and made the production resolution Atlas-or-null. The existing test
# corpus stubs ``get_embedding_store`` directly to validate semantic
# behaviour, so it still needs a *non-null* resolved driver name to
# clear the ``semantic_retrieval_available()`` gate in the fallback
# dispatch. We register a tiny in-process fake under the name
# ``test_in_memory`` below and pin the env var to it. Tests that
# exercise the graceful-fallback path explicitly override this with
# ``monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "null")``.
os.environ.setdefault("EMBEDDING_STORE_DRIVER", "test_in_memory")
# Match production/default Integral posture before app.main import: jvagent's
# own HTTP surface stays off. Setting it here (not inside main via bare
# ``os.environ[...] =``) keeps the env-leak autouse fixture quiet.
os.environ.setdefault("JVAGENT_EMBED_ENDPOINTS_DISABLED", "1")


# Register the in-process test driver before any other imports of
# ``app.services.retrieval`` so the registry sees it on first lookup.
# The driver is a duck-typed EmbeddingStore: upsert / soft_delete /
# hard_delete are no-ops, search returns [], count returns 0.
# Production code that monkeypatches ``get_embedding_store`` replaces
# this lookup with its own FakeStore; the registry-level resolution
# still reports a non-null driver and the dispatch layer accepts the
# call.
def _register_test_in_memory_driver() -> None:  # pragma: no cover — test plumbing
    from app.services.retrieval import register_driver

    class _TestInMemoryStore:
        driver_name = "test_in_memory"

        async def upsert(self, *_args, **_kwargs):
            return None

        async def search(self, *_args, **_kwargs):
            return []

        async def soft_delete(self, *_args, **_kwargs):
            return None

        async def hard_delete(self, *_args, **_kwargs):
            return None

        async def count(self):
            return 0

    try:
        register_driver("test_in_memory", _TestInMemoryStore())
    except ValueError:
        # Already registered (e.g. when conftest is re-imported via
        # _fresh_retrieval reset cycles in some tests).
        pass


_register_test_in_memory_driver()


# Environment variables a test may legitimately leave changed.
#
# ``PYTEST_CURRENT_TEST`` is pytest's own bookkeeping — it carries the current
# test id plus the phase (``setup`` / ``call`` / ``teardown``), so it differs
# between the two snapshots of EVERY test by construction. It is not a leak.
#
# Keep the rest of this list empty unless a value genuinely must outlive the
# test that set it; the point of the guard below is that it almost never must.
_ENV_LEAK_ALLOWED: frozenset = frozenset({"PYTEST_CURRENT_TEST"})


@pytest.fixture(autouse=True)
def _fail_on_environment_leak():
    """Fail the test that leaves an env var changed behind it.

    `main`'s full suite was red for two days because
    ``test_agentive_whatsapp.py`` set ``INTEGRAL_SERVICE_KEY`` with a bare
    ``os.environ[...] = …`` and never restored it. Every later test on that
    xdist worker then authenticated against the leaked key, and
    ``test_agentive_chat.py`` failed with a 401 that had nothing to do with
    the code it was testing.

    Nothing caught it. ``reset_per_test_global_state`` already clears the
    service-key MEMO between tests — which made it worse, because the clean
    re-read picked the polluted value straight back up. And the failure was
    invisible on PRs, which run only the ``smoke`` marker: neither file is in
    it. The two only met on the same worker because CI groups by file
    (``--dist loadfile``) while the local default scatters them.

    So the guard belongs here rather than in any marker or CI flag: this fires
    in every invocation, for every test, and names the test that leaked rather
    than the innocent one that fails ten files later. ``monkeypatch`` restores
    itself, so correct usage never trips it — only raw mutation does.
    """
    before = dict(os.environ)
    yield
    after = dict(os.environ)
    if before == after:
        return

    changed = []
    for key in sorted(set(before) | set(after)):
        if key in _ENV_LEAK_ALLOWED:
            continue
        was, now = before.get(key), after.get(key)
        if was == now:
            continue
        # Values can be secrets; report the transition shape, not the payload.
        changed.append(
            f"  {key}: {'<unset>' if was is None else '<set>'} -> "
            f"{'<unset>' if now is None else '<set>'}"
        )
    if not changed:
        return

    # Restore, so one leaky test does not cascade into a wall of unrelated
    # failures — the report below is the actionable signal.
    os.environ.clear()
    os.environ.update(before)
    raise AssertionError(
        "test leaked os.environ changes to the rest of the worker:\n"
        + "\n".join(changed)
        + "\n\nUse `monkeypatch.setenv` / `monkeypatch.delenv`, which restore "
        "at teardown. A bare `os.environ[...] = ...` persists for the life of "
        "the xdist worker process and will fail an unrelated test in another "
        "file — see the service-key incident in tests/test_agentive_whatsapp.py."
    )


@pytest.fixture(autouse=True)
def _ensure_test_in_memory_driver_registered():
    """Re-register ``test_in_memory`` if a prior test reset the retrieval module.

    Tests in ``test_retrieval_embedding_store.py`` flush
    ``sys.modules['app.services.retrieval*']`` to validate the registry
    auto-registration logic. That reset wipes the ``test_in_memory``
    entry conftest added at session start; subsequent tests that rely
    on it then see ``semantic_retrieval_available()`` return False and
    skip the very behaviour they're trying to exercise. This autouse
    fixture re-runs the registration if the slot is missing.
    """
    try:
        from app.services.retrieval import get_registered_drivers
    except Exception:
        yield
        return
    if "test_in_memory" not in get_registered_drivers():
        _register_test_in_memory_driver()
    yield


# Force (do NOT ``setdefault``) the test service key for the whole session.
# ``setdefault`` deferred to any ambient ``INTEGRAL_SERVICE_KEY`` a developer
# had exported (or in a sourced .env), so tests that rely on this module-level
# value — e.g. ``test_agentive_chat`` — would send the hardcoded test key while
# the validator compared it against the developer's real key, failing with
# ``invalid_service_key``. Tests must never inherit an ambient secret; force it.
# (Per-test overrides still work: they use ``monkeypatch.setenv``, which layers
# on top of and restores relative to this value.)
os.environ["INTEGRAL_SERVICE_KEY"] = (
    "integral-test-service-key-for-agentive-only__________"
)

# Eager-load Settings / dotenv so backend/.env keys (ADMIN_*, JVSPATIAL_*, …)
# are already in os.environ before ``_fail_on_environment_leak`` snapshots.
# Otherwise the first test that imports ``app.config`` / ``app.main`` looks
# like it leaked those vars (body passes; teardown ERROR). CI has no .env,
# so this is a no-op there.
try:
    import app.config  # noqa: E402, F401
except Exception:
    pass

# When BASE_URL is set, run against live server; otherwise in-process
TEST_BASE_URL = os.getenv("BASE_URL", "http://test")
USE_LIVE_SERVER = TEST_BASE_URL != "http://test"  # Signal that we're in test mode


# Modules that must exercise the real HTTP signup/login path.
_HTTP_AUTH_MODULES = frozenset(
    {
        "test_email_verification",
        "test_password_reset",
        "test_crud_users",
    }
)

# Default test credentials shared by fast-auth and HTTP-auth paths.
_DEFAULT_TEST_EMAIL = "test@example.com"
_DEFAULT_TEST_PASSWORD = "testpassword123"
_DEFAULT_TEST_NAME = "Test User"


def _remove_path_best_effort(
    path: str,
    *,
    retries: int = 5,
    poll_interval: float = 0.01,
    max_wait: float = 0.2,
) -> None:
    """Delete file/dir, polling until gone instead of fixed sleeps."""
    deadline = time.monotonic() + max_wait
    for attempt in range(retries):
        try:
            if not os.path.exists(path):
                return
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except Exception:
            if attempt == retries - 1:
                return
        if not os.path.exists(path):
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(poll_interval)


# Test modules that require disk library packages in the catalog.
_LIBRARY_MODULES = frozenset(
    {
        "test_seeded_library_packages",
        "test_seeded_packages_v2",
        "test_library_seed_metadata",
        "test_workspace_init",
        "test_member_field_graph_contiguousness",
        "test_content_profile_loader",
        "test_content_profile_loader_v3",
        "test_workspaces_create_with_profile",
        "test_profile_authoring",
        "test_app_bundles_invariants",
        "test_app_bundled_agents",
        "test_app_bundled_skills",
        "test_requires_apps",
        "test_hot_load_roundtrip",
        "test_track_templates_manifest",
        "test_connector_hooks_runtime",
        "test_content_profile_merge",
        "test_workspace_scope_manifest",
        "test_schema_edit_isolation",
        "test_content_profile_derive_from_app",
        "test_content_profile_derive_from_track",
        "test_content_profile_revert_app",
        "test_content_profile_revert_track",
        "test_content_profile_detach_app",
        "test_content_profile_detach_track",
        "test_agent_insights_workspace_scope",
        "test_cross_app_relations",
        "test_integral_onboard_user",
        "test_type_hint_tag_resolution",
    }
)

# Library-dependent modules quarantined under tests/domain_apps/ (opt-in via
# ``pytest -m domain_app`` or ``INTEGRAL_INCLUDE_DOMAIN_APPS=1``).
_DOMAIN_LIBRARY_MODULES = frozenset(
    {
        "test_phase17_hr_payroll_install",
        "test_hr_payroll_manifests",
        "test_discovery_profile",
        "test_contacts_taxonomy",
        "test_anchored_privileged_tracks",
        "test_task_entry_type",
        "test_finance_profile_install",
        "test_payroll_privilege",
        "test_time_off_workflow",
        "test_quickbooks_provenance",
        "test_quickbooks_privilege",
        "test_privileged_track_access",
        "test_privileged_track_policy_set",
        "test_crm_pm_workspace_init_e2e",
        "test_e2e_consulting_workflow",
        "test_content_factory_install",
        "test_engineer_role_access_audit",
        "test_crm_bundle_tool_email_match",
        "test_crm_bundle_tool_pricing",
    }
)

# Pure unit modules — no jvspatial DB / graph bootstrap (see setup_test_db +
# bind_fresh_graph_context_for_async_tests). Keeps validator, compile, grep-gate,
# and tokenizer tests off the per-test ``ensure_integral_app_graph`` path.
_UNIT_MODULES = frozenset(
    {
        "test_validators_common",
        "test_keyword_match",
        "test_hooks_schemas",
        "test_hooks_resolver",
        "test_hooks_registry",
        "test_hooks_errors",
        "test_hooks_declarative",
        "test_hooks_tool_dispatch",
        "test_provenance_schema",
        "test_credential_crypto",
        "test_content_profile_v2_compile",
        "test_content_profile_signature",
        "test_calendar_view_validation",
        "test_kanban_column_enum_sync",
        "test_kanban_view_config",
        "test_content_disposition",
        "test_url_safety",
        "test_avatar_resize",
        "test_native_import_guards",
        "test_template_var_resolvers",
        "test_staging_language",
        "test_tooling_catalogue",
        "test_tooling_manifest",
        "test_integral_use_cases",
        "test_integral_skill_placement",
        "test_ai_chat_draft_boundary",
        "test_jvagent_update_mode",
        "test_content_profile_plugins",
        "test_view_contract_catalog",
        "test_view_card_template",
        "test_charset_utf8",
        "test_entry_type_field_cache",
        "test_entry_type_change_fields",
        "test_model_credential_indexes",
        "test_mutation_provenance",
        "test_mutation_rollback",
        "test_api",
        "test_api_sync",
        "test_staging_display",
        "test_skill_tool_consistency",
        "test_change_event_no_bypass",
        "test_content_profile_loader",
        "test_content_profile_loader_v3",
        "test_invitation_email",
        "test_manifest_runtime_repair",
        "test_workspace_scope_manifest",
    }
)

# Subprocess / full-repo grep gates / coverage subprocess invocations — not
# needed on every dev loop (CI sets INTEGRAL_RUN_SLOW_TESTS=1).
_SLOW_MODULES = frozenset(
    {
        "test_phase8_coverage_gate",
        "test_phase_2_7_endpoint_coverage",
        "test_substrate_drift_gate",
        "test_jvspatial_convention_compliance",
        "test_app_rename_grep_gates",
        "test_phase8_drift",
        "test_no_legacy_helper_calls",
        "test_connector_sync_e2e",
        "test_retrieval_reembed_hooks",
        "test_retrieval_embedding_store",
        "test_pgvector_driver",
        "test_seeded_library_packages",
        "test_seeded_packages_v2",
        "test_graph_node_attachments",
        "test_app_bundles_invariants",
        "test_content_profile_registries",
        "test_agent_profile_patches",
        "test_manifest_tools_hooks",
        "test_manifest_internal_dedup",
        "test_track_templates_manifest",
        "test_anchor_seed",
        "test_content_profiles_import",
        "test_notification_paths",
        "test_app_install_token",
        "test_attachment_phase2",
        "test_attachment_phase4",
        "test_attachment_phase5",
        "test_attachment_phase6",
    }
)


def _test_is_unit(request) -> bool:
    return request.node.get_closest_marker("unit") is not None


# Fixtures that require ``setup_test_db`` + ``bind_fresh_graph_context`` (directly
# or via dependency chain). Sync tests that do not request any of these skip the
# per-test JsonDB swap and ``ensure_integral_app_graph`` — grep/compile/AST gates
# only.
_EXPLICIT_GRAPH_FIXTURES = frozenset(
    {
        "client",
        "authenticated_client",
        "authenticated_admin_client",
        "test_user",
        "test_user2",
        "second_user",
        "auth_token",
        "auth_token2",
        "jwt_for_test_user",
        "jwt_for_second_user",
        "second_user_client",
        "library_catalog_seeded",
    }
)


def _test_needs_per_test_db(request) -> bool:
    """True when the test body needs a fresh prime+log JsonDB and graph shell."""
    if _test_is_unit(request):
        return False
    if USE_LIVE_SERVER:
        return False
    if _TEST_DB_KIND in ("postgres", "postgresql"):
        return True
    test_fn = getattr(request.node, "function", None) or getattr(
        request.node, "obj", None
    )
    if test_fn is not None and inspect.iscoroutinefunction(test_fn):
        return True
    return bool(set(request.fixturenames) & _EXPLICIT_GRAPH_FIXTURES)


def pytest_configure(config):
    """Allow full suite when domain-app tests should run alongside substrate."""
    if os.getenv("INTEGRAL_INCLUDE_DOMAIN_APPS", "0") == "1":
        config.option.markexpr = ""


def pytest_collection_modifyitems(config, items):
    """Default-prune slow tests; auto-mark library / unit / slow modules."""
    run_slow = os.getenv("INTEGRAL_RUN_SLOW_TESTS", "0") == "1"
    skip_slow = None
    if not run_slow:
        skip_slow = pytest.mark.skip(
            reason="slow test pruned by default; set INTEGRAL_RUN_SLOW_TESTS=1"
        )
    for item in items:
        mod = getattr(item.module, "__name__", "").rsplit(".", 1)[-1]
        if mod in _UNIT_MODULES and "unit" not in item.keywords:
            item.add_marker(pytest.mark.unit)
        if mod in _SLOW_MODULES and "slow" not in item.keywords:
            item.add_marker(pytest.mark.slow)
        if skip_slow is not None and "slow" in item.keywords:
            item.add_marker(skip_slow)
        if mod in _LIBRARY_MODULES and "library" not in item.keywords:
            item.add_marker(pytest.mark.library)
        if mod in _DOMAIN_LIBRARY_MODULES and "library" not in item.keywords:
            item.add_marker(pytest.mark.library)


def _reset_per_test_global_state() -> None:
    """Reset module-global caches that leak between tests."""
    from app.middleware.permissions_cache import reset_permissions_cache

    reset_permissions_cache()
    try:
        from jvspatial.env import clear_load_env_cache

        clear_load_env_cache()
    except Exception:
        pass
    try:
        from app.agentive.middleware.service_auth import _reset_service_key_cache

        _reset_service_key_cache()
    except Exception:
        pass
    try:
        from app.services.connectors.registry import reset_sync_registry

        reset_sync_registry()
    except Exception:
        pass


def _reset_change_event_logger_cache() -> None:
    try:
        from app.services.change_event_logger import reset_for_testing

        reset_for_testing()
    except Exception:
        pass


@pytest.fixture(scope="function", autouse=True)
def reset_per_test_global_state(request):
    """Reset permission / auth / connector / env caches between tests."""
    needs_db = _test_needs_per_test_db(request)
    _reset_per_test_global_state()
    if needs_db:
        _reset_change_event_logger_cache()
    yield
    _reset_per_test_global_state()
    if needs_db:
        _reset_change_event_logger_cache()


# Lazy import - only import when needed to avoid blocking during pytest collection
_app = None
_User = None


def _invalidate_cached_app() -> None:
    """Drop the cached ASGI app so the next ``get_app()`` sees fresh server state."""
    global _app
    _app = None


def _rebind_server_to_prime_db() -> None:
    """Point jvspatial Server + AuthService at the current prime database."""
    from jvspatial.core.context import GraphContext
    from jvspatial.db import get_prime_database

    prime_ctx = GraphContext(database=get_prime_database())
    try:
        from app.main import server

        if getattr(server, "_graph_context", None) is not None:
            server._graph_context = prime_ctx
        auth_service = getattr(server, "_auth_service", None)
        if auth_service is not None:
            auth_service.context = prime_ctx
            auth_service._blacklist_cache.clear()
    except Exception:
        pass


def _swap_prime_and_log_dbs(*, prime_path: str, logs_path: str) -> None:
    """Install per-test JSON databases on the global DatabaseManager."""
    from jvspatial.db import get_database_manager
    from jvspatial.db.factory import create_database

    from app.services.db_metrics import RequestDBOpRecorder

    manager = get_database_manager()
    # observe=True + RequestDBOpRecorder keep X-DB-Round-Trip-Count counting
    # on the swapped-in per-test prime DB (see app.services.db_metrics for the
    # BaseHTTPMiddleware context rationale). Log DB stays unwrapped — prime
    # round-trips only.
    fresh_db = create_database(
        db_type="json",
        base_path=prime_path,
        observe=True,
        metrics=RequestDBOpRecorder(),
    )
    manager._prime_database = fresh_db
    manager._databases["prime"] = fresh_db
    fresh_log_db = create_database(db_type="json", base_path=logs_path)
    manager._databases["logs"] = fresh_log_db


_GRAPH_SHELL_TEMPLATE_ROOT = (
    Path(__file__).resolve().parent.parent
    / ".pytest_cache"
    / f"graph_shell_{_XDIST_WORKER}"
)


def _graph_shell_template_ready() -> bool:
    return (_GRAPH_SHELL_TEMPLATE_ROOT / ".ready").is_file()


async def _async_build_graph_shell_template() -> None:
    """Build a once-per-worker JsonDB snapshot of the Integral app shell."""
    prime_t = _GRAPH_SHELL_TEMPLATE_ROOT / "prime"
    logs_t = _GRAPH_SHELL_TEMPLATE_ROOT / "logs"
    if _GRAPH_SHELL_TEMPLATE_ROOT.exists():
        shutil.rmtree(_GRAPH_SHELL_TEMPLATE_ROOT)
    prime_t.mkdir(parents=True)
    logs_t.mkdir(parents=True)

    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db import get_database_manager
    from jvspatial.db.factory import create_database

    from app.services.app_graph import ensure_integral_app_graph

    manager = get_database_manager()
    fresh_db = create_database(db_type="json", base_path=str(prime_t))
    manager._prime_database = fresh_db
    manager._databases["prime"] = fresh_db
    manager._databases["logs"] = create_database(db_type="json", base_path=str(logs_t))
    set_default_context(GraphContext(database=fresh_db))
    await ensure_integral_app_graph(include_library=False)
    (_GRAPH_SHELL_TEMPLATE_ROOT / ".ready").write_text("1", encoding="utf-8")


def _ensure_graph_shell_template_built() -> None:
    if _graph_shell_template_ready():
        return
    import asyncio as _asyncio

    try:
        _asyncio.get_event_loop().run_until_complete(
            _async_build_graph_shell_template()
        )
    except RuntimeError:
        _asyncio.new_event_loop().run_until_complete(
            _async_build_graph_shell_template()
        )


def _install_test_dbs_from_graph_shell_template(
    *, prime_path: str, logs_path: str
) -> None:
    """Reset per-test JsonDB dirs by copying the session graph-shell snapshot."""
    _ensure_graph_shell_template_built()
    prime_t = _GRAPH_SHELL_TEMPLATE_ROOT / "prime"
    logs_t = _GRAPH_SHELL_TEMPLATE_ROOT / "logs"
    pp = Path(prime_path)
    lp = Path(logs_path)
    if pp.exists():
        shutil.rmtree(pp)
    if lp.exists():
        shutil.rmtree(lp)
    pp.mkdir(parents=True)
    lp.mkdir(parents=True)
    shutil.copytree(prime_t, pp, dirs_exist_ok=True)
    shutil.copytree(logs_t, lp, dirs_exist_ok=True)
    _swap_prime_and_log_dbs(prime_path=prime_path, logs_path=logs_path)


def _test_uses_graph_shell_template(request) -> bool:
    """True when per-test DB should clone the session shell snapshot."""
    if _TEST_DB_KIND in ("postgres", "postgresql") or USE_LIVE_SERVER:
        return False
    if request.node.get_closest_marker("fresh_graph") is not None:
        return False
    return True


def get_app():
    """Lazy import of app to avoid blocking during pytest collection.

    This ensures the app is imported after all API modules are loaded,
    so endpoints registered via @endpoint decorators are available.
    Rebinds the jvspatial Server on every access so ASGI handlers always
    target the per-test prime database (``_app`` is cached across calls
    within a test after ``_invalidate_cached_app`` reset).
    """
    global _app
    if _app is None:
        from app.main import app as _app_instance

        _app = _app_instance
    _rebind_server_to_prime_db()
    return _app


def get_user_node():
    """Lazy import of User."""
    global _User
    if _User is None:
        from app.models.nodes import User as _User_class

        _User = _User_class
    return _User


async def _verify_library_catalog_seeded() -> None:
    """Sync disk library packages into the catalog registry."""
    from app.models.nodes import ContentProfile
    from app.services.app_graph import ensure_library_catalog_seeded
    from app.services.content_profile_library_sync import (
        load_library_profiles_with_issues_cached,
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    await ensure_library_catalog_seeded()
    expected = len(load_library_profiles_with_issues_cached()[0])
    listed = await ContentProfile.find({"context.library_package": True})
    if listed is None:
        count = 0
    elif isinstance(listed, list):
        count = len(listed)
    else:
        count = 1
    if count < expected:
        raise RuntimeError(
            f"library catalog incomplete: cataloged={count} expected>={expected}"
        )


def _test_needs_library_catalog(request) -> bool:
    """True when an async test body requires disk library packages in the DB."""
    if request.node.get_closest_marker("library") is None:
        return False
    test_fn = getattr(request.node, "function", None) or getattr(
        request.node, "obj", None
    )
    return test_fn is not None and inspect.iscoroutinefunction(test_fn)


@pytest.fixture(scope="session", autouse=True)
def _pg_test_db_bootstrap():
    """Drop + re-create the per-worker Postgres test DB at session start.

    Only runs when ``INTEGRAL_TEST_DB=postgres``. Connects to the
    default ``postgres`` admin DB, drops any existing per-worker test DB
    (releasing prior connections first), then creates it fresh. Without
    this every session would inherit residue from the previous run.

    Per-test isolation in PG mode is a separate, larger piece of work
    (see top-of-file note). This fixture only guarantees a clean DB at
    *session* start.
    """
    if _TEST_DB_KIND not in ("postgres", "postgresql") or USE_LIVE_SERVER:
        yield
        return
    import asyncio as _asyncio

    try:
        import asyncpg  # type: ignore
    except ImportError as exc:  # pragma: no cover — env misconfig
        raise RuntimeError(
            "INTEGRAL_TEST_DB=postgres requires asyncpg. "
            "Install via `uv sync --frozen --extra dev --extra test` (asyncpg is a "
            "project dependency)."
        ) from exc

    # Parse DSN to swap in the admin DB name while keeping creds + host.
    from urllib.parse import urlparse, urlunparse

    parts = urlparse(_PG_TEST_DSN)
    admin_dsn = urlunparse(parts._replace(path="/postgres"))
    target_db = parts.path.lstrip("/") or _PG_TEST_DB_NAME

    async def _bootstrap() -> None:
        conn = await asyncpg.connect(admin_dsn)
        try:
            # Terminate other sessions on the target DB so DROP succeeds.
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                target_db,
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{target_db}"')
            await conn.execute(f'CREATE DATABASE "{target_db}"')
        finally:
            await conn.close()

    try:
        _asyncio.get_event_loop().run_until_complete(_bootstrap())
    except RuntimeError:
        # Fresh loop in some pytest-asyncio configurations.
        _asyncio.new_event_loop().run_until_complete(_bootstrap())
    yield


@pytest.fixture
async def postgres_raw_db():
    """Direct PostgresDB for spike/contract primitives (not the app graph)."""
    if _TEST_DB_KIND not in ("postgres", "postgresql"):
        pytest.skip("Postgres-only (set INTEGRAL_TEST_DB=postgres)")
    from jvspatial.db.factory import create_database

    database = create_database(db_type="postgres")
    yield database
    await database.close()


@pytest.fixture(autouse=True)
def _plugin_discovery_bootstrap():
    """Re-register code plugins (view/field types) before every test.

    Production registers these at real app startup (``app/main.py``); test
    requests bypass the lifespan entirely, so any test that compiles a
    manifest declaring a plugin-registered view type (e.g. ``payroll_filings``'s
    ``editable_table``/``action_bar``, ``region_system``'s ``chart_region``/
    ``tree_region``/``form_region``/``layout_container``/``static_content``/
    ``summary_tiles``) needs discovery to have already run, or
    ``load_library_profiles_with_issues`` fails ``canonical_manifest_fingerprint``
    for every library package touching that view type — not just the one a
    given test file is about — because fingerprinting walks the whole
    library set.

    Deliberately **function-scoped**, not session-scoped: ``discover_and_
    register_plugins()`` is guarded by a module-level "already discovered"
    flag, so once it's run once, later calls are no-ops — they do NOT
    re-register anything. Some existing test files pop specific view types
    from the registry directly at teardown (e.g.
    ``test_region_system_plugin.py``) *and* call ``reset_discovered_for_
    tests()``, which would otherwise leave the registry empty and the
    discovery flag cleared for whatever test runs next in the same session.
    Re-running discovery before every test is cheap (no-op unless something
    reset it) and makes the whole suite self-healing regardless of test
    order, instead of every manifest-touching test file needing its own
    copy of this same registration dance.
    """
    from app.services.content_profile_plugins import discover_and_register_plugins

    discover_and_register_plugins()
    yield


@pytest.fixture(scope="session", autouse=True)
def _graph_shell_template_bootstrap():
    """Warm the per-worker Integral graph-shell JsonDB snapshot once per session."""
    if _TEST_DB_KIND in ("postgres", "postgresql") or USE_LIVE_SERVER:
        yield
        return
    _ensure_graph_shell_template_built()
    yield


@pytest.fixture(scope="function", autouse=True)
def setup_test_db(tmp_path, monkeypatch, request):
    """Set up test database for each test function.

    Using function scope ensures test isolation - each test gets a fresh database.
    Skipped when BASE_URL is set (running against live server).
    Skipped when the test does not need a per-test DB (``unit`` marker, or a
    sync grep/compile test with no graph-scoped fixtures).

    Per-test ``tmp_path`` directories avoid JsonDB atomic-write races that occur
    when one test's teardown deletes ``test_integral_db`` while the next test's
    ``ensure_integral_app_graph`` catalog sync is still writing edges.

    In Postgres test mode the JsonDB swap is a no-op — per-test isolation
    in that mode is a known follow-up (see _pg_test_db_bootstrap docstring).
    """
    if not _test_needs_per_test_db(request):
        yield
        return
    if USE_LIVE_SERVER:
        yield
        return
    if _TEST_DB_KIND in ("postgres", "postgresql"):
        yield
        return
    prime_path = tmp_path / "prime"
    logs_path = tmp_path / "logs"
    monkeypatch.setenv("JVSPATIAL_DB_PATH", str(prime_path))
    monkeypatch.setenv("JVSPATIAL_LOG_DB_PATH", str(logs_path))
    try:
        from jvspatial.env import clear_load_env_cache

        clear_load_env_cache()
    except Exception:
        pass

    _invalidate_cached_app()
    if _test_uses_graph_shell_template(request):
        _install_test_dbs_from_graph_shell_template(
            prime_path=str(prime_path),
            logs_path=str(logs_path),
        )
    else:
        _swap_prime_and_log_dbs(
            prime_path=str(prime_path),
            logs_path=str(logs_path),
        )
    yield


@pytest.fixture(scope="function", autouse=True)
async def bind_fresh_graph_context_for_async_tests(setup_test_db, request):
    """Bind the per-test prime DB to the asyncio task running the test.

    Graph shell setup runs here (not in the sync ``setup_test_db`` fixture) so
    we avoid ``asyncio.run()`` crossing event-loop boundaries under
    pytest-asyncio, which caused sporadic auth/DB desync and OSError EINVAL on
    some platforms.

    Skipped when the test does not need a per-test DB (``unit`` marker, or a
    sync grep/compile test with no graph-scoped fixtures).
    """
    if not _test_needs_per_test_db(request):
        yield
        return
    if USE_LIVE_SERVER:
        yield
        return
    token = None
    from jvspatial.core.context import GraphContext, set_default_context
    from jvspatial.db import get_prime_database

    token = set_default_context(GraphContext(database=get_prime_database()))
    _rebind_server_to_prime_db()
    if _test_needs_library_catalog(request):
        from app.services.app_graph import ensure_integral_app_graph
        from app.services.content_profile_library_sync import (
            reset_library_profiles_cache_for_testing,
        )

        await ensure_integral_app_graph(include_library=False)
        reset_library_profiles_cache_for_testing()
        await _verify_library_catalog_seeded()
        _rebind_server_to_prime_db()
    elif not _test_uses_graph_shell_template(request):
        from app.services.app_graph import ensure_integral_app_graph

        await ensure_integral_app_graph(include_library=False)
    yield
    if token is not None:
        try:
            from jvspatial.core.context import _default_context_var

            _default_context_var.reset(token)
        except (ValueError, LookupError):
            pass


@pytest.fixture(scope="session")
def _session_library_specs_cache():
    """Warm the library YAML parse cache once per pytest session."""
    from app.services.content_profile_library_sync import (
        load_library_profiles_with_issues_cached,
    )

    load_library_profiles_with_issues_cached()
    yield


@pytest.fixture(autouse=True)
def _ensure_session_library_cache(_session_library_specs_cache):
    """Ensure session library cache is initialized before any test runs."""
    yield


@pytest.fixture
async def library_catalog_seeded(bind_fresh_graph_context_for_async_tests):
    """Explicit opt-in marker: library catalog is seeded in ``bind_fresh_graph_context``."""
    yield


def _module_uses_http_auth(request) -> bool:
    mod = getattr(request.node, "module", None)
    if mod is None:
        return False
    return mod.__name__.rsplit(".", 1)[-1] in _HTTP_AUTH_MODULES


def _mint_test_jwt(
    *,
    user_id: str,
    email: str,
    name: str,
    roles: Optional[list] = None,
    permissions: Optional[list] = None,
) -> str:
    """Mint a JWT accepted by TestAuthBypassMiddleware — no login bcrypt verify."""
    import jwt as pyjwt

    from app.config import settings

    payload = {
        "user_id": user_id,
        "sub": user_id,
        "email": email,
        "name": name,
        "roles": roles or ["user"],
        "permissions": list(permissions or []),
    }
    return pyjwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def _bootstrap_test_user_fast(
    *,
    email: str = _DEFAULT_TEST_EMAIL,
    password: str = _DEFAULT_TEST_PASSWORD,
    name: str = _DEFAULT_TEST_NAME,
):
    """Create AuthUser + User + personal workspace without HTTP signup."""
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email=email, password=password)
    )
    auth_user_id = user_response.id
    now = datetime.now().isoformat()
    user_node = await User.create(
        user_id=auth_user_id,
        display_name=name,
        created_at=now,
        updated_at=now,
    )
    with contextlib.suppress(Exception):
        await catalog_user(user_node)
    with contextlib.suppress(Exception):
        await ensure_personal_workspace(user_node)
    token = _mint_test_jwt(
        user_id=auth_user_id,
        email=email,
        name=name,
        # Force the default role in the JWT. jvspatial makes the first AuthUser
        # in an empty DB a platform admin; leaving that in the token made
        # ``authenticated_client`` silently bypass every admin-gated check.
        # Admin coverage uses ``authenticated_admin_client`` (explicit mint).
        roles=["user"],
        permissions=list(user_response.permissions or []),
    )
    return token, user_node


@pytest.fixture
async def client(bind_fresh_graph_context_for_async_tests):
    """Create an async HTTP client for testing.

    For in-process tests: uses ASGITransport. Auth may fail due to httpx/ASGI
    scope handling - run with BASE_URL=http://localhost:4000 against a live
    server for full auth coverage.
    """
    if USE_LIVE_SERVER:
        async_client = AsyncClient(base_url=TEST_BASE_URL, timeout=10.0)
        try:
            yield async_client
        finally:
            try:
                if not async_client.is_closed:
                    await async_client.aclose()
            except Exception:
                pass
        return
    app = get_app()
    transport = ASGITransport(app=app)
    async_client = AsyncClient(
        transport=transport, base_url="http://test", timeout=10.0
    )
    try:
        yield async_client
    finally:
        try:
            if not async_client.is_closed:
                await async_client.aclose()
        except Exception:
            pass


class _UserLike:
    """Minimal user object for tests when DB access unavailable (e.g. live server)."""

    def __init__(
        self, id: str, display_name: str = "", user_id: str = None, email: str = ""
    ):
        self.id = id
        self.user_id = user_id or id
        self.display_name = display_name
        self.email = email


@pytest.fixture
async def test_user(authenticated_client):
    """Create a test user for data access.

    When using live server (BASE_URL set), fetches user from /api/auth/me.
    Otherwise creates User from AuthUser in local DB.
    """
    if USE_LIVE_SERVER:
        try:
            resp = await authenticated_client.get("/api/auth/me")
            if resp.status_code == 200:
                data = resp.json().get("user", {})
                uid = data.get("id") or data.get("user_id", "")
                yield _UserLike(
                    id=uid,
                    display_name=data.get("display_name", "Test User"),
                    user_id=uid,
                    email="test@example.com",
                )
                return
        except Exception as e:
            print(f"Warning: Failed to get user from /api/auth/me: {e}")
        yield _UserLike(id="", display_name="Test User")
        return

    User = get_user_node()
    email = "test@example.com"
    try:
        from jvspatial.api.auth.models import User as AuthUser

        auth_users = await AuthUser.find({"context.email": email})
        if auth_users:
            auth_user = auth_users[0]
            user_nodes = await User.find({"context.user_id": auth_user.id})
            if not user_nodes:
                user_nodes = await User.find({"user_id": auth_user.id})
            if user_nodes:
                from app.services.personal_workspace import ensure_personal_workspace

                await ensure_personal_workspace(user_nodes[0])
                yield user_nodes[0]
                return
            created_at = auth_user.created_at
            if isinstance(created_at, datetime):
                created_at_str = created_at.isoformat()
            else:
                created_at_str = datetime.now().isoformat()
            user = await User.create(
                user_id=auth_user.id,
                display_name=auth_user.name or "Test User",
                created_at=created_at_str,
            )
            from app.services.personal_workspace import ensure_personal_workspace

            await ensure_personal_workspace(user)
            yield user
            return
    except Exception as e:
        print(f"Warning: Failed to create User from AuthUser: {e}")
    try:
        user = await User.create(
            user_id=f"test_user_{email}",
            display_name="Test User",
            created_at=datetime.now().isoformat(),
        )
        yield user
    except Exception as e:
        print(f"Warning: Failed to create test user: {e}")
        yield None


@pytest.fixture
async def test_user2(client):
    """Create a second test user for collaboration tests.

    In-process: ``_bootstrap_test_user_fast`` (AuthUser + User + personal
    workspace, same as ``authenticated_client``). Live server / HTTP-auth
    modules: real ``POST /api/auth/signup`` so signup behaviour stays
    exercisable.

    The User node's ``user_id`` MUST match the JWT ``user_id`` claim
    (AuthUser id) so ``COLLABORATES_ON`` edges and ``resolve_principal_id``
    agree in share/collab tests.
    """
    email = "test2@example.com"
    password = "testpassword123"
    display_name = "Test User 2"

    if not USE_LIVE_SERVER:
        try:
            _token, user_node = await _bootstrap_test_user_fast(
                email=email,
                password=password,
                name=display_name,
            )
            yield user_node
            return
        except Exception as e:
            print(f"Warning: Fast bootstrap for test user 2 failed: {e}")

    User = get_user_node()
    try:
        await client.post(
            "/api/auth/signup",
            json={"email": email, "password": password, "name": display_name},
            timeout=10.0,
        )
    except Exception:
        pass

    try:
        from jvspatial.api.auth.models import User as AuthUser

        auth_users = await AuthUser.find({"context.email": email})
        if auth_users:
            auth_user = auth_users[0]
            user_nodes = await User.find({"context.user_id": auth_user.id})
            if not user_nodes:
                user_nodes = await User.find({"user_id": auth_user.id})
            if user_nodes:
                from app.services.personal_workspace import ensure_personal_workspace

                await ensure_personal_workspace(user_nodes[0])
                yield user_nodes[0]
                return
            created_at = auth_user.created_at
            if isinstance(created_at, datetime):
                created_at_str = created_at.isoformat()
            else:
                created_at_str = datetime.now().isoformat()
            user = await User.create(
                user_id=auth_user.id,
                display_name=auth_user.name or display_name,
                created_at=created_at_str,
            )
            from app.services.personal_workspace import ensure_personal_workspace

            await ensure_personal_workspace(user)
            yield user
            return
    except Exception as e:
        print(f"Warning: Failed to resolve test user 2 from AuthUser: {e}")

    try:
        user = await User.create(
            user_id=f"test_user2_{email}",
            display_name=display_name,
            created_at=datetime.now().isoformat(),
        )
        yield user
    except Exception as e:
        print(f"Warning: Failed to create test user 2: {e}")
        yield None


@pytest.fixture
async def auth_token(client, test_user):
    """Get authentication token for test user (JWT string)."""
    email = getattr(test_user, "email", None) or "test@example.com"
    password = "testpassword123"
    name = getattr(test_user, "display_name", None) or "Test User"

    try:
        signup_resp = await client.post(
            "/api/auth/signup",
            json={"email": email, "password": password, "name": name},
            timeout=10.0,
        )
        if signup_resp.status_code in (200, 201):
            token = _extract_token(signup_resp.json())
            if token:
                return token
    except Exception:
        pass

    try:
        login_resp = await client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            timeout=10.0,
        )
        if login_resp.status_code == 200:
            token = _extract_token(login_resp.json())
            if token:
                return token
    except Exception:
        pass

    return None


def _extract_token(data: dict) -> Optional[str]:
    """Extract JWT token from auth response (signup or login)."""
    return (
        data.get("access_token")
        or data.get("token")
        or data.get("accessToken")
        or data.get("jwt_token")
    )


@pytest.fixture
async def authenticated_client(request, bind_fresh_graph_context_for_async_tests):
    """Create an authenticated HTTP client with built-in auth.

    Default: direct AuthService bootstrap (no HTTP signup). Modules in
    ``_HTTP_AUTH_MODULES`` keep the real ``/api/auth/signup`` path.
    """
    email = _DEFAULT_TEST_EMAIL
    password = _DEFAULT_TEST_PASSWORD
    name = _DEFAULT_TEST_NAME
    token = None

    if USE_LIVE_SERVER or _module_uses_http_auth(request):
        http_client = AsyncClient(base_url=TEST_BASE_URL, timeout=10.0)
        if not USE_LIVE_SERVER:
            app = get_app()
            http_client = AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                timeout=10.0,
            )
        try:
            signup_resp = await http_client.post(
                "/api/auth/signup",
                json={"email": email, "password": password, "name": name},
                timeout=10.0,
            )
            if signup_resp.status_code in (200, 201):
                token = _extract_token(signup_resp.json())
            if not token:
                login_resp = await http_client.post(
                    "/api/auth/login",
                    json={"email": email, "password": password},
                    timeout=10.0,
                )
                if login_resp.status_code == 200:
                    token = _extract_token(login_resp.json())
        except Exception:
            pass
        finally:
            if not USE_LIVE_SERVER:
                await http_client.aclose()
    else:
        token, _user = await _bootstrap_test_user_fast(
            email=email, password=password, name=name
        )

    if token:
        if USE_LIVE_SERVER:
            auth_client = AsyncClient(
                base_url=TEST_BASE_URL,
                timeout=10.0,
                headers={"Authorization": f"Bearer {token}"},
            )
        else:
            app = get_app()
            transport = ASGITransport(app=app)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                timeout=10.0,
                headers={"Authorization": f"Bearer {token}"},
            )
        try:
            yield auth_client
        finally:
            await auth_client.aclose()
    else:
        pytest.fail(
            "authenticated_client: failed to obtain JWT — bootstrap or HTTP auth "
            "did not return a token; see traceback above"
        )


@pytest.fixture
async def authenticated_admin_client(client, test_user):
    """Authenticated httpx client whose JWT carries ``roles=["admin", "user"]``.

    The TestAuthBypassMiddleware (``backend/app/middleware/test_auth.py``)
    decodes the JWT and pre-sets ``request.state.user.roles`` from the
    payload. Admin-gated endpoints (e.g. ``/api/admin/profiles/*``) check
    ``"admin" in request.state.user.roles``. We mint a JWT directly with
    admin roles to avoid mutating the shared AuthUser row.
    """
    user_id = getattr(test_user, "user_id", None) or getattr(test_user, "id", "")
    if not user_id:
        # Live-server fallback: yield unauthenticated client; tests should
        # skip via fixture availability.
        yield client
        return

    payload = {
        "user_id": user_id,
        "email": getattr(test_user, "email", "") or "test@example.com",
        "name": getattr(test_user, "display_name", "") or "Test User",
        "roles": ["admin", "user"],
        "permissions": [],
    }
    token = _mint_test_jwt(
        user_id=user_id,
        email=payload["email"],
        name=payload["name"],
        roles=payload["roles"],
        permissions=payload["permissions"],
    )

    if USE_LIVE_SERVER:
        admin_client = AsyncClient(
            base_url=TEST_BASE_URL,
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )
    else:
        app = get_app()
        transport = ASGITransport(app=app)
        admin_client = AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )
    try:
        yield admin_client
    finally:
        try:
            if not admin_client.is_closed:
                await admin_client.aclose()
        except Exception:
            pass


@pytest.fixture
def signed_service_headers():
    """Helper that returns valid signed service-auth headers for a given user_id.

    Reuses app.agentive.middleware.service_auth.compute_signature so production
    and test code share a single canonical signing site (drift impossible).
    """
    import time as _time

    def _make(user_id: str, *, ts: Optional[int] = None) -> dict:
        from app.agentive.middleware.service_auth import (
            _reset_service_key_cache,
            compute_signature,
        )

        # Ensure the cached service key reflects the current env (tests may
        # monkeypatch INTEGRAL_SERVICE_KEY between fixture loads).
        _reset_service_key_cache()
        key = os.environ.get(
            "INTEGRAL_SERVICE_KEY",
            "integral-test-service-key-for-agentive-only__________",
        )
        ts = ts if ts is not None else int(_time.time())
        sig = compute_signature(str(ts), user_id)
        return {
            "X-Integral-Service-Key": key,
            "X-Integral-User-Id": user_id,
            "X-Integral-Timestamp": str(ts),
            "X-Integral-Signature": sig,
        }

    return _make


@pytest.fixture
async def auth_token2(client, test_user2):
    """Get authentication token for second test user."""
    if not test_user2:
        return None

    email = getattr(test_user2, "email", None) or "test2@example.com"
    password = "testpassword123"
    name = getattr(test_user2, "display_name", None) or "Test User 2"

    if not USE_LIVE_SERVER:
        try:
            from jvspatial.api.auth.models import UserLogin

            from app.api.auth import _get_auth_service

            token_response = await _get_auth_service().login_user(
                UserLogin(email=email, password=password)
            )
            return token_response.access_token
        except Exception:
            pass

    try:
        signup_resp = await client.post(
            "/api/auth/signup",
            json={"email": email, "password": password, "name": name},
            timeout=10.0,
        )
        if signup_resp.status_code in (200, 201):
            token = _extract_token(signup_resp.json())
            if token:
                return token
    except Exception:
        pass

    try:
        login_resp = await client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            timeout=10.0,
        )
        if login_resp.status_code == 200:
            token = _extract_token(login_resp.json())
            if token:
                return token
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Phase 2 W4 fixtures (added by Plan 02-03 — canonical owner).
#
# Consumers:
#   - backend/tests/test_events_ws.py        (Plan 02-03 — WS cross-tenant)
#   - backend/tests/test_audit_log_query.py  (Plan 02-02 — audit cross-tenant)
#
# These fixtures provide JWT strings (signed exactly like /api/auth/login) and
# an authenticated httpx client for a second user, enabling cross-tenant
# permission-filter tests without standing up two parallel test servers.
#
# Per the WS endpoint at backend/app/api/events_ws.py, JWT must be passed in
# the URL query string (?token=<jwt>) — httpx `Authorization: Bearer` headers
# are not a substitute for WS connection auth. The same JWT shape is reused
# for the audit-log httpx client (cross-tenant test in 02-02).
# ---------------------------------------------------------------------------


@pytest.fixture
async def second_user(test_user2):
    """Alias for test_user2 with a stable cross-plan name.

    test_user2 already creates a User node distinct from test_user; this alias
    aligns with the names referenced in 02-02's audit-log cross-tenant test
    and 02-03's WS cross-tenant test. The underlying fixture is reused as-is.
    """
    yield test_user2


@pytest.fixture
async def jwt_for_test_user(test_user, auth_token):
    """JWT string for test_user, mirroring the production /api/auth/login claim shape.

    Prefers the live ``auth_token`` fixture (which signs via the real auth path —
    proves production/test interchangeability). Falls back to a directly-encoded
    JWT only if ``auth_token`` is unavailable (live-server mode without a
    working /api/auth/login response).
    """
    if auth_token:
        return auth_token

    # Direct-encode fallback — mirrors jvspatial auth JWT shape (user_id claim).
    try:
        from jose import jwt as jose_jwt

        from app.config import settings

        auth_user_id = getattr(test_user, "user_id", None) or getattr(
            test_user, "id", ""
        )
        if not auth_user_id:
            return None
        return jose_jwt.encode(
            {"user_id": auth_user_id, "email": "test@example.com"},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )
    except Exception:
        return None


@pytest.fixture
async def jwt_for_second_user(second_user, auth_token2):
    """JWT string for second_user.

    Prefers ``auth_token2`` (signed via /api/auth/login). Direct-encode fallback
    mirrors ``jwt_for_test_user``.
    """
    if auth_token2:
        return auth_token2

    try:
        from jose import jwt as jose_jwt

        from app.config import settings

        if second_user is None:
            return None
        auth_user_id = getattr(second_user, "user_id", None) or getattr(
            second_user, "id", ""
        )
        if not auth_user_id:
            return None
        return jose_jwt.encode(
            {"user_id": auth_user_id, "email": "test2@example.com"},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )
    except Exception:
        return None


@pytest.fixture
async def second_user_client(client, second_user, jwt_for_second_user):
    """An httpx AsyncClient authenticated as second_user.

    Mirrors the existing ``authenticated_client`` pattern but uses second_user's
    JWT instead of test_user's. Closes cleanly on fixture teardown.

    If no JWT can be minted (e.g. test_user2 fixture failed in this environment),
    yields the unauthenticated ``client`` so dependent tests fail with a clear
    permission-denied / empty-result signal rather than a fixture collection
    error.
    """
    if not jwt_for_second_user:
        yield client
        return

    if USE_LIVE_SERVER:
        auth_client = AsyncClient(
            base_url=TEST_BASE_URL,
            timeout=10.0,
            headers={"Authorization": f"Bearer {jwt_for_second_user}"},
        )
    else:
        app = get_app()
        transport = ASGITransport(app=app)
        auth_client = AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=10.0,
            headers={"Authorization": f"Bearer {jwt_for_second_user}"},
        )
    try:
        yield auth_client
    finally:
        try:
            if not auth_client.is_closed:
                await auth_client.aclose()
        except Exception:
            pass
